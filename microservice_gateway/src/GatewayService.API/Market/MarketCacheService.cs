using System.Collections.Concurrent;
using System.Text.Json;
using Microsoft.Extensions.Caching.Distributed;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed class MarketCacheService : IMarketCacheService
{
    private readonly IDistributedCache _cache;
    private readonly ILogger<MarketCacheService> _log;

    // In-process stampede protection: coalesces concurrent cache-miss
    // requests for the same key into a single factory execution.
    // Key → Lazy<Task<object>>
    private readonly ConcurrentDictionary<string, Lazy<Task<object>>> _inflight = new();

    public MarketCacheService(IDistributedCache cache, ILogger<MarketCacheService> log)
    {
        _cache = cache;
        _log   = log;
    }

    /// <inheritdoc />
    public async Task<T?> GetAsync<T>(string key, CancellationToken ct = default) where T : class
    {
        try
        {
            var bytes = await _cache.GetAsync(key, ct);
            if (bytes is null) return null;
            return JsonSerializer.Deserialize<T>(bytes);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Cache get failed for key {CacheKey}", key);
            return null;
        }
    }

    /// <inheritdoc />
    public async Task SetAsync<T>(string key, T value, TimeSpan ttl, CancellationToken ct = default) where T : class
    {
        try
        {
            var bytes = JsonSerializer.SerializeToUtf8Bytes(value);
            await _cache.SetAsync(key, bytes,
                new DistributedCacheEntryOptions
                {
                    AbsoluteExpirationRelativeToNow = ttl,
                },
                ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Cache set failed for key {CacheKey}", key);
        }
    }

    /// <inheritdoc />
    public async Task<bool> SetIfNotExistsAsync(
        string key, string value, TimeSpan ttl, CancellationToken ct = default)
    {
        try
        {
            // Note: IDistributedCache has no atomic NX operation.
            // This implementation is not strictly atomic across multiple instances,
            // but the worst-case outcome is a small number of redundant ingests,
            // which is acceptable (idempotent operation).
            var existing = await _cache.GetAsync(key, ct);
            if (existing is not null) return false;

            await _cache.SetStringAsync(key, value,
                new DistributedCacheEntryOptions
                {
                    AbsoluteExpirationRelativeToNow = ttl,
                },
                ct);
            return true;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Cache SetIfNotExists failed for key {CacheKey}", key);
            // Fail open: treat as if key does not exist so the caller can proceed.
            return true;
        }
    }

    /// <inheritdoc />
    public async Task RemoveAsync(string key, CancellationToken ct = default)
    {
        try
        {
            await _cache.RemoveAsync(key, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Cache remove failed for key {CacheKey}", key);
        }
    }

    /// <inheritdoc />
    public async Task<T> GetOrCreateAsync<T>(
        string key,
        TimeSpan ttl,
        Func<Task<T>> factory,
        CancellationToken ct = default) where T : class
    {
        // Fast path: cache hit
        var cached = await GetAsync<T>(key, ct);
        if (cached is not null)
            return cached;

        // Slow path: coalesce concurrent misses into a single factory call
        var lazy = _inflight.GetOrAdd(
            key,
            _ => new Lazy<Task<object>>(async () => await factory()));

        try
        {
            var result = (T)await lazy.Value;
            // Store in cache (errors are swallowed inside SetAsync)
            await SetAsync(key, result, ttl, ct);
            return result;
        }
        finally
        {
            // Remove from in-flight map regardless of success or failure
            _inflight.TryRemove(new KeyValuePair<string, Lazy<Task<object>>>(key, lazy));
        }
    }
}
