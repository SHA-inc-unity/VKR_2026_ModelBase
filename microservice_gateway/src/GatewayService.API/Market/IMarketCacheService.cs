namespace GatewayService.API.Market;

/// <summary>
/// Caching layer for the market API.
///
/// Uses <see cref="Microsoft.Extensions.Caching.Distributed.IDistributedCache"/>
/// (Redis in production, in-memory in tests) with:
/// - JSON serialisation
/// - In-process stampede protection via per-key lazy tasks
/// - SetIfNotExists for distributed ingest-lock semantics
/// - Graceful fallback: cache errors are logged and the factory is called directly
/// </summary>
public interface IMarketCacheService
{
    /// <summary>Returns the cached value or null on miss / deserialisation error.</summary>
    Task<T?> GetAsync<T>(string key, CancellationToken ct = default) where T : class;

    /// <summary>Stores the value with the given absolute TTL.</summary>
    Task SetAsync<T>(string key, T value, TimeSpan ttl, CancellationToken ct = default) where T : class;

    /// <summary>
    /// Atomic-ish "set if not exists" for distributed locks.
    /// Returns <c>true</c> when the key was newly created (lock acquired),
    /// <c>false</c> when the key already existed (lock already held).
    /// </summary>
    Task<bool> SetIfNotExistsAsync(string key, string value, TimeSpan ttl, CancellationToken ct = default);

    /// <summary>Removes a cache entry.</summary>
    Task RemoveAsync(string key, CancellationToken ct = default);

    /// <summary>
    /// Returns the cached value, or executes <paramref name="factory"/> once,
    /// stores the result, and returns it.
    ///
    /// In-process stampede protection ensures that even when thousands of
    /// concurrent requests arrive for the same cache miss, the factory is
    /// executed exactly once per key per process.
    /// </summary>
    Task<T> GetOrCreateAsync<T>(
        string key,
        TimeSpan ttl,
        Func<Task<T>> factory,
        CancellationToken ct = default) where T : class;
}
