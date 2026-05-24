using System.Collections.Concurrent;
using System.Diagnostics;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <summary>
/// In-process request coalescing + concurrency-control layer for chart requests.
/// Implements <see cref="IChartService"/> as a decorator over <see cref="ChartService"/>.
///
/// Responsibilities:
/// <list type="number">
///   <item>Normalize the request key (uppercase symbol, lower-case canonical timeframe/limit).</item>
///   <item>Fast-path cache check — no TCS/semaphore allocation on cache hit.</item>
///   <item>Coalesce concurrent identical requests into a single downstream pipeline call:
///         all clients that arrive with the same key while a pipeline is in flight await
///         the shared <see cref="TaskCompletionSource{T}"/>; only the first (creator) runs
///         the actual pipeline.</item>
///   <item>Enforce per-class and global concurrency limits via <see cref="SemaphoreSlim"/>.</item>
///   <item>Isolate per-client cancellation: cancelling one HTTP request does not cancel the
///         shared in-flight pipeline — other waiters continue to receive the result.</item>
///   <item>Log: cache-hit, new-inflight, coalesced-waiters, queue-wait-time,
///         downstream duration, SERVICE_BUSY.</item>
/// </list>
///
/// Graceful Redis fallback: the fast-path cache check is done via
/// <see cref="IMarketCacheService"/>, which silently falls back to no-op when Redis is
/// unavailable. In that case coalescing still protects the service within a single process.
/// </summary>
public sealed class ChartRequestQueue : IChartService
{
    // ── In-flight registry ────────────────────────────────────────────────

    private sealed class InFlightEntry
    {
        public readonly TaskCompletionSource<ServiceResult<ChartResponse>> Tcs =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        /// <summary>Number of waiters after the creator (Interlocked).</summary>
        public int WaiterCount;
    }

    private readonly ConcurrentDictionary<string, InFlightEntry> _inflight = new();

    // ── Dependencies ──────────────────────────────────────────────────────

    private readonly ChartService            _inner;
    private readonly IMarketCacheService     _cache;
    private readonly MarketSettings          _settings;
    private readonly ILogger<ChartRequestQueue> _log;

    // ── Concurrency semaphores ────────────────────────────────────────────

    private readonly SemaphoreSlim _heavySemaphore;
    private readonly SemaphoreSlim _totalSemaphore;

    // Cache key format mirrors ChartService so the fast-path hits the same Redis key.
    private const string ChartCacheKeyFmt = "market:chart:{0}:{1}:{2}:v1";

    public ChartRequestQueue(
        ChartService inner,
        IMarketCacheService cache,
        IOptions<MarketSettings> settings,
        ILogger<ChartRequestQueue> log)
    {
        _inner    = inner;
        _cache    = cache;
        _settings = settings.Value;
        _log      = log;

        _heavySemaphore = new SemaphoreSlim(
            _settings.QueueHeavyConcurrency, _settings.QueueHeavyConcurrency);
        _totalSemaphore = new SemaphoreSlim(
            _settings.QueueTotalConcurrency, _settings.QueueTotalConcurrency);
    }

    /// <inheritdoc />
    public async Task<ServiceResult<ChartResponse>> GetChartAsync(
        string symbol, string timeframe, int limit, CancellationToken ct = default)
    {
        // ── 1. Normalize ──────────────────────────────────────────────────

        var normalizedSymbol = symbol.ToUpperInvariant();
        var normalizedTf     = timeframe.ToLowerInvariant();
        var requestKey       = $"{normalizedSymbol}:{normalizedTf}:{limit}:v1";

        // ── 2. Fast-path cache check (avoids TCS allocation on cache hit) ─

        if (TimeframeMap.TryGetById(normalizedTf, out var tfInfo))
        {
            var cacheKey = string.Format(
                ChartCacheKeyFmt, normalizedSymbol, tfInfo.BybitInterval, limit);
            var cached = await _cache.GetAsync<ChartResponse>(cacheKey, ct)
                ?? await _inner.TryGetCachedChartAsync(normalizedSymbol, tfInfo, limit, ct);
            if (cached is not null)
            {
                _log.LogDebug("[queue:cache-hit] {Key}", requestKey);
                return ServiceResult<ChartResponse>.Ok(cached);
            }
        }

        // ── 3. Coalesce: try to become the creator ────────────────────────

        var entry    = new InFlightEntry();
        var existing = _inflight.GetOrAdd(requestKey, entry);

        if (!ReferenceEquals(existing, entry))
        {
            // Waiter path: await the shared TCS, respecting our own client CT.
            // Cancelling this client does NOT affect other waiters or the pipeline.
            var waitersNow = Interlocked.Increment(ref existing.WaiterCount);
            _log.LogDebug("[queue:coalesced] {Key} waiters={Waiters}", requestKey, waitersNow);

            var queueSw = Stopwatch.StartNew();
            try
            {
                var result = await existing.Tcs.Task.WaitAsync(ct);
                queueSw.Stop();
                _log.LogDebug("[queue:waiter-done] {Key} waitMs={WaitMs}",
                    requestKey, queueSw.ElapsedMilliseconds);
                return result;
            }
            catch (OperationCanceledException)
            {
                _log.LogDebug("[queue:waiter-cancelled] {Key}", requestKey);
                throw;
            }
        }

        // ── 4. Creator path: acquire semaphore, run pipeline ──────────────

        var isHeavy      = tfInfo?.Class == TimeframeClass.Heavy;
        var downstreamSw = Stopwatch.StartNew();
        bool totalAcquired = false;
        bool heavyAcquired = false;

        // Use a standalone work CTS — not linked to the creator's client CT.
        // This ensures the pipeline completes even if the creator disconnects,
        // so all waiters still receive a result.
        using var workCts = new CancellationTokenSource(
            TimeSpan.FromSeconds(_settings.IngestKafkaTimeoutSeconds + 60));

        try
        {
            // Acquire global concurrency slot
            totalAcquired = await _totalSemaphore.WaitAsync(
                TimeSpan.FromSeconds(_settings.QueueMaxWaitSeconds), workCts.Token);

            if (!totalAcquired)
            {
                var busy = ServiceResult<ChartResponse>.Fail(
                    "SERVICE_BUSY: too many concurrent market chart requests");
                entry.Tcs.SetResult(busy);
                _log.LogWarning("[queue:busy-total] {Key}", requestKey);
                return busy;
            }

            // Acquire heavy-class concurrency slot
            if (isHeavy)
            {
                heavyAcquired = await _heavySemaphore.WaitAsync(
                    TimeSpan.FromSeconds(_settings.QueueMaxWaitSeconds), workCts.Token);

                if (!heavyAcquired)
                {
                    var busy = ServiceResult<ChartResponse>.Fail(
                        "SERVICE_BUSY: too many concurrent heavy-timeframe chart requests");
                    entry.Tcs.SetResult(busy);
                    _log.LogWarning("[queue:busy-heavy] {Key}", requestKey);
                    return busy;
                }
            }

            _log.LogDebug("[queue:downstream-start] {Key}", requestKey);

            var result = await _inner.GetChartAsync(
                normalizedSymbol, normalizedTf, limit, workCts.Token);

            downstreamSw.Stop();
            _log.LogInformation(
                "[queue:downstream-done] {Key} status={Status} candles={Candles} elapsedMs={ElapsedMs}",
                requestKey,
                result.Value?.Status ?? (result.IsSuccess ? "ok" : "error"),
                result.Value?.Candles?.Count ?? 0,
                downstreamSw.ElapsedMilliseconds);

            entry.Tcs.SetResult(result);
            return result;
        }
        catch (Exception ex)
        {
            downstreamSw.Stop();
            _log.LogError(ex,
                "[queue:downstream-error] {Key} elapsedMs={ElapsedMs}",
                requestKey, downstreamSw.ElapsedMilliseconds);

            var fail = ServiceResult<ChartResponse>.Fail(
                $"INTERNAL_ERROR: {ex.GetType().Name}: {ex.Message}");
            entry.Tcs.SetResult(fail);
            return fail;
        }
        finally
        {
            if (heavyAcquired) _heavySemaphore.Release();
            if (totalAcquired) _totalSemaphore.Release();

            // Remove our entry so the next request after completion starts fresh.
            // Cast to ICollection to get the conditional Remove(key+value) overload,
            // so a concurrent new entry for the same key is not accidentally removed.
            ((ICollection<KeyValuePair<string, InFlightEntry>>)_inflight)
                .Remove(new KeyValuePair<string, InFlightEntry>(requestKey, entry));
        }
    }
}
