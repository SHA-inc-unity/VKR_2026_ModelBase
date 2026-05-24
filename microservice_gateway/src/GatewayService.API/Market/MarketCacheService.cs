using System.Collections.Concurrent;
using System.Text.Json;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed class MarketCacheService : IMarketCacheService
{
    private readonly IDistributedCache _cache;
    private readonly IMemoryCache _hotCache;
    private readonly ILogger<MarketCacheService> _log;
    private readonly TimeSpan _hotCacheTtl;

    // In-process stampede protection: coalesces concurrent cache-miss
    // requests for the same key into a single factory execution.
    // Key → Lazy<Task<object>>
    private readonly ConcurrentDictionary<string, Lazy<Task<object>>> _inflight = new();

    public MarketCacheService(
        IDistributedCache cache,
        IMemoryCache hotCache,
        IOptions<MarketSettings> settings,
        ILogger<MarketCacheService> log)
    {
        _cache       = cache;
        _hotCache    = hotCache;
        _log         = log;
        _hotCacheTtl = TimeSpan.FromSeconds(Math.Max(1, settings.Value.LocalHotCacheSeconds));
    }

    /// <inheritdoc />
    public async Task<T?> GetAsync<T>(string key, CancellationToken ct = default) where T : class
    {
        try
        {
            if (_hotCache.TryGetValue<T>(key, out var hot) && hot is not null)
                return hot;

            var bytes = await _cache.GetAsync(key, ct);
            if (bytes is null) return null;

            var value = JsonSerializer.Deserialize<T>(bytes);
            if (value is not null)
                _hotCache.Set(key, value, _hotCacheTtl);

            return value;
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
            _hotCache.Set(key, value, MemoryTtlFor(ttl));

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

            _hotCache.Set(key, value, MemoryTtlFor(ttl));
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
            _hotCache.Remove(key);
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

    private TimeSpan MemoryTtlFor(TimeSpan ttl)
    {
        return ttl <= _hotCacheTtl ? ttl : _hotCacheTtl;
    }
}
