using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public partial class ChartService : IChartService
{
    private enum SyncHydrationDisposition
    {
        RowsReady,
        KeepExistingRows,
        Failed,
    }

    private sealed record SyncHydrationResult(
        SyncHydrationDisposition Disposition,
        RowsFetchResult? Rows = null,
        string? Error = null);

    // ── Synchronous wait policy ───────────────────────────────────────────
    // The backend no longer emits a "pending" status. Instead, when ingest is
    // already in flight (or starts during this request) we block and poll the
    // data-service for the latest window until either rows arrive, the budget
    // is exhausted, or the cooldown lock signals a failed ingest. After the
    // budget we return SERVICE_BUSY so the controller emits a clean 503.

    // Cache key patterns (note the `{exchange}` segment — without it,
    // switching exchange would serve stale candles from another exchange's
    // cache, and a Bybit ingest-lock would block a Binance request):
    //   market:chart:{exchange}:{symbol}:{bybitInterval}:{limit}:v2
    //   market:ingest-lock:{exchange}:{symbol}:{bybitInterval}
    private const string ChartKeyFmt         = "market:chart:{0}:{1}:{2}:{3}:v2";
    private const string IngestLockFmt       = "market:ingest-lock:{0}:{1}:{2}";
    private const string IngestInProgress    = "inprogress";
    private const string IngestErrorCooldown = "error_cooldown";

    private readonly IMarketConfigService  _config;
    private readonly IMarketCacheService   _cache;
    private readonly IDataServiceClient    _data;
    private readonly MarketSettings        _settings;
    private readonly ILogger<ChartService> _log;

    public ChartService(
        IMarketConfigService config,
        IMarketCacheService cache,
        IDataServiceClient data,
        IOptions<MarketSettings> settings,
        ILogger<ChartService> log)
    {
        _config   = config;
        _cache    = cache;
        _data     = data;
        _settings = settings.Value;
        _log      = log;
    }

    /// <inheritdoc />
    public virtual async Task<ServiceResult<ChartResponse>> GetChartAsync(
        string symbol, string timeframe, int limit,
        string exchange = "bybit", CancellationToken ct = default)
    {
        // ── 1. Validate ───────────────────────────────────────────────────────

        var symbolUpper = symbol.ToUpperInvariant();
        var exchangeKey = DataServiceClient.NormalizeExchange(exchange);

        // _config.IsKnownSymbolAsync is sourced from the Bybit instrument list.
        // For non-Bybit exchanges we skip the strict whitelist for now — typos
        // will surface as a downstream 503 rather than a 400 until we add
        // per-exchange instrument lists. (See plan: Phase 1 / risks.)
        if (exchangeKey == "bybit" && !await _config.IsKnownSymbolAsync(symbolUpper, exchangeKey, ct))
            return ServiceResult<ChartResponse>.Fail(
                $"INVALID_SYMBOL: '{symbol}' is not in the active symbol list");

        if (!TimeframeMap.TryGetById(timeframe, out var tfInfo))
            return ServiceResult<ChartResponse>.Fail(
                $"INVALID_TIMEFRAME: '{timeframe}' is not a supported timeframe. " +
                $"Valid values: {string.Join(", ", TimeframeMap.All.Select(t => t.Id))}");

        if (!CandleCountGrid.IsValid(limit, tfInfo.Class))
        {
            var allowed = string.Join(", ", CandleCountGrid.ForClass(tfInfo.Class));
            return ServiceResult<ChartResponse>.Fail(
                $"INVALID_LIMIT: {limit} is not in the allowed candle count grid " +
                $"for '{timeframe}' ({tfInfo.Class}). Allowed: [{allowed}]");
        }

        // ── 2. Cache check ────────────────────────────────────────────────────

        var cached = await TryGetCachedChartAsync(symbolUpper, tfInfo, limit, exchangeKey, ct);
        if (cached is not null)
            return ServiceResult<ChartResponse>.Ok(cached);

        // ── 3. Check ingest lock ──────────────────────────────────────────────
        // "inprogress"     → another request already triggered ingest, wait for it
        // "error_cooldown" → previous ingest failed; short retry window before re-attempt
        // null             → no ingest in flight, proceed normally

        var ingestKey    = string.Format(IngestLockFmt, exchangeKey, symbolUpper, tfInfo.BybitInterval);
        var ingestActive = await _cache.GetAsync<string>(ingestKey, ct);
        if (ingestActive == IngestErrorCooldown)
        {
            return ServiceResult<ChartResponse>.Fail(
                "SERVICE_BUSY: Previous ingest attempt failed; retry after the cooldown window");
        }

        if (ingestActive is not null)
        {
            // Another request is already hydrating this symbol/timeframe.
            // Block here and poll the latest window until rows arrive or we
            // exhaust the budget; never return "pending" to the client.
            var waited = await WaitForInflightIngestAsync(
                ingestKey, symbolUpper, tfInfo, limit, exchangeKey, ct);

            if (waited.IsFailure)
            {
                return BuildRowsFailureResult(symbolUpper, timeframe, limit, waited, "latest_rows");
            }

            if (waited.HasRows)
            {
                return ServiceResult<ChartResponse>.Ok(
                    await BuildChartResponseAsync(
                        symbolUpper, timeframe, limit, tfInfo, waited, exchangeKey, ct));
            }

            return ServiceResult<ChartResponse>.Fail(
                "SERVICE_BUSY: Chart ingest is still running for this symbol/timeframe; try again shortly");
        }

        // ── 4. Query latest fixed-width window ───────────────────────────────

        var latestRows = await _data.GetLatestWindowRowsAsync(
            symbolUpper,
            tfInfo.BybitInterval,
            tfInfo.StepMs,
            limit,
            DataServiceClient.ChartProjectionColumns,
            exchangeKey,
            ct);

        if (latestRows.IsFailure)
        {
            return BuildRowsFailureResult(symbolUpper, timeframe, limit, latestRows, "latest_rows");
        }

        if (latestRows.IsClaimCheck)
        {
            // The window exists upstream but is too large to fit a Kafka
            // message — there is nothing we can do server-side to recover,
            // so surface a real failure instead of a fake "pending".
            return ServiceResult<ChartResponse>.Fail(
                "DATA_SOURCE_UNAVAILABLE: Chart payload exceeds Kafka size limit; use a smaller limit");
        }

        if (latestRows.IsEmpty)
        {
            var initialEndMs   = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var initialStartMs = initialEndMs - (long)limit * tfInfo.StepMs * _settings.IngestWindowMultiplier;
            var hydrationResult = await TryHydrateWindowSynchronouslyAsync(
                ingestKey,
                symbolUpper,
                timeframe,
                tfInfo,
                limit,
                initialStartMs,
                initialEndMs,
                fallbackRows: null,
                exchangeKey,
                ct);

            return hydrationResult.Disposition switch
            {
                SyncHydrationDisposition.RowsReady => ServiceResult<ChartResponse>.Ok(
                    await BuildChartResponseAsync(
                        symbolUpper,
                        timeframe,
                        limit,
                        tfInfo,
                        hydrationResult.Rows!,
                        exchangeKey,
                        ct)),
                SyncHydrationDisposition.Failed => ServiceResult<ChartResponse>.Fail(
                    hydrationResult.Error ?? "SERVICE_BUSY: Unable to hydrate chart right now"),
                _ => ServiceResult<ChartResponse>.Fail(
                    "SERVICE_BUSY: Chart hydration did not complete in time; try again shortly"),
            };
        }

        // ── 5. Determine time window ──────────────────────────────────────────

        var endMs   = latestRows.Rows[^1].TimestampMs;
        var startMs = endMs - (long)(limit - 1) * tfInfo.StepMs;
        var rowsForResponse = latestRows;

        // Freshness gate: when the latest row is older than 2 × stepMs we
        // refresh up to "now" instead of returning a stale window. Without
        // this the chart can lock on rows from days/hours ago when the
        // market watcher hasn't been keeping a given exchange's table fresh.
        var isStale = IsLatestRowStale(rowsForResponse, tfInfo);
        if (isStale)
        {
            var nowMs            = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var refreshStartMs   = endMs + tfInfo.StepMs;
            endMs                = nowMs;
            startMs              = endMs - (long)(limit - 1) * tfInfo.StepMs;
            _log.LogInformation(
                "Chart latest-row stale for {Exchange}/{Symbol}/{Interval}: latestTs={LatestMs} ageMs={AgeMs}; refreshing [{RefreshStart}..{RefreshEnd}]",
                exchangeKey,
                symbolUpper,
                tfInfo.BybitInterval,
                rowsForResponse.Rows[^1].TimestampMs,
                nowMs - rowsForResponse.Rows[^1].TimestampMs,
                refreshStartMs,
                endMs);
        }

        if (isStale || NeedsHydration(rowsForResponse, limit))
        {
            var hydrationResult = await TryHydrateWindowSynchronouslyAsync(
                ingestKey,
                symbolUpper,
                timeframe,
                tfInfo,
                limit,
                startMs,
                endMs,
                rowsForResponse,
                exchangeKey,
                ct);

            if (hydrationResult.Disposition == SyncHydrationDisposition.Failed)
            {
                return ServiceResult<ChartResponse>.Fail(
                    hydrationResult.Error ?? "SERVICE_BUSY: Unable to hydrate chart right now");
            }

            if (hydrationResult.Disposition == SyncHydrationDisposition.RowsReady &&
                hydrationResult.Rows is not null)
            {
                rowsForResponse = hydrationResult.Rows;
            }
        }

        // ── 7. Build response ─────────────────────────────────────────────────

        return ServiceResult<ChartResponse>.Ok(
            await BuildChartResponseAsync(
                symbolUpper,
                timeframe,
                limit,
                tfInfo,
                rowsForResponse,
                exchangeKey,
                ct));
    }

    private async Task<ChartResponse> BuildChartResponseAsync(
        string symbol,
        string timeframe,
        int limit,
        TimeframeInfo tfInfo,
        RowsFetchResult rowsResult,
        string exchange,
        CancellationToken ct,
        string? cacheKeyOverride = null,
        TimeSpan? cacheTtlOverride = null)
    {
        var candles = rowsResult.Rows
            .OrderBy(r => r.TimestampMs)
            .Select(r => new CandleDto(r.TimestampMs, r.Open, r.High, r.Low, r.Close,
                                        r.Volume, r.Turnover))
            .ToList();

        // Window-scoped coverage: fraction of the requested limit we actually received.
        // More accurate for the client than the global table CoveragePct,
        // which reflects all-time history rather than the requested window.
        var windowCoverageFraction = (double)candles.Count / limit;
        var isFullCoverage         = windowCoverageFraction >= _settings.FullCoverageThreshold;

        string status;
        int? retryAfterMs = null;

        status = isFullCoverage ? "ok" : "partial";
        if (!isFullCoverage)
            retryAfterMs = _settings.IngestRetryAfterMs;

        var coverageLabel = isFullCoverage ? "full" : "partial";

        var response = new ChartResponse
        {
            Symbol    = symbol,
            Timeframe = timeframe,
            Limit     = limit,
            Candles   = candles,
            Meta = new ChartMetaDto
            {
                Requested = limit,
                Available = candles.Count,
                FromMs    = candles.Count > 0 ? candles[0].T : 0L,
                ToMs      = candles.Count > 0 ? candles[^1].T : 0L,
                Coverage  = coverageLabel,
            },
            Status       = status,
            RetryAfterMs = retryAfterMs,
        };

        // Cache only when data is complete; partial results get a shorter TTL.
        // Historical pagination overrides both (immutable page → long TTL under
        // a cursor-scoped key) via the optional override parameters.
        var cacheTtl = cacheTtlOverride ?? CacheTtlFor(tfInfo, isFullCoverage);
        var cacheKey = cacheKeyOverride ?? BuildCacheKey(symbol, tfInfo, limit, exchange);
        await _cache.SetAsync(cacheKey, response, cacheTtl, ct);

        return response;
    }
}
