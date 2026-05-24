using System.Diagnostics;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public class ChartService : IChartService
{
    private enum HydrationTriggerResult
    {
        Started,
        AlreadyInProgress,
        Failed,
    }

    // Cache key patterns:
    //   market:chart:{symbol}:{bybitInterval}:{limit}:v1
    //   market:ingest-lock:{symbol}:{bybitInterval}
    private const string ChartKeyFmt         = "market:chart:{0}:{1}:{2}:v1";
    private const string IngestLockFmt       = "market:ingest-lock:{0}:{1}";
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
        string symbol, string timeframe, int limit, CancellationToken ct = default)
    {
        // ── 1. Validate ───────────────────────────────────────────────────────

        var symbolUpper = symbol.ToUpperInvariant();

        if (!await _config.IsKnownSymbolAsync(symbolUpper, ct))
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

        var cached = await TryGetCachedChartAsync(symbolUpper, tfInfo, limit, ct);
        if (cached is not null)
            return ServiceResult<ChartResponse>.Ok(cached);

        // ── 3. Check ingest lock ──────────────────────────────────────────────
        // "inprogress"     → another request already triggered ingest, wait for it
        // "error_cooldown" → previous ingest failed; short retry window before re-attempt
        // null             → no ingest in flight, proceed normally

        var ingestKey    = string.Format(IngestLockFmt, symbolUpper, tfInfo.BybitInterval);
        var ingestActive = await _cache.GetAsync<string>(ingestKey, ct);
        if (ingestActive == IngestErrorCooldown)
        {
            return ServiceResult<ChartResponse>.Fail(
                "SERVICE_BUSY: Previous ingest attempt failed; retry after the cooldown window");
        }

        if (ingestActive is not null)
        {
            var hint = ingestActive == IngestErrorCooldown
                ? "Previous ingest attempt failed; a retry will happen after the cooldown"
                : "Ingest already in progress for this symbol/timeframe";
            return ServiceResult<ChartResponse>.Ok(
                BuildPendingResponse(symbolUpper, timeframe, limit, hint));
        }

        // ── 4. Query latest fixed-width window ───────────────────────────────

        var latestRows = await _data.GetLatestWindowRowsAsync(
            symbolUpper,
            tfInfo.BybitInterval,
            tfInfo.StepMs,
            limit,
            ct);

        if (latestRows.IsFailure)
        {
            return BuildRowsFailureResult(symbolUpper, timeframe, limit, latestRows, "latest_rows");
        }

        if (latestRows.IsClaimCheck)
        {
            return ServiceResult<ChartResponse>.Ok(
                BuildPendingResponse(symbolUpper, timeframe, limit,
                    "Data payload too large; use a smaller limit or wait for streaming path"));
        }

        if (latestRows.IsEmpty)
        {
            var initialEndMs   = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var initialStartMs = initialEndMs - (long)limit * tfInfo.StepMs * _settings.IngestWindowMultiplier;
            var triggerResult = await TryTriggerWindowHydrationAsync(
                ingestKey, symbolUpper, tfInfo, limit, initialStartMs, initialEndMs, ct);

            if (triggerResult == HydrationTriggerResult.Failed)
            {
                return ServiceResult<ChartResponse>.Fail(
                    "SERVICE_BUSY: Unable to start chart hydration right now");
            }

            var pendingReason = triggerResult == HydrationTriggerResult.Started
                ? "No data available locally; ingest triggered"
                : "Ingest already in progress for this symbol/timeframe";

            return ServiceResult<ChartResponse>.Ok(BuildPendingResponse(
                symbolUpper, timeframe, limit, pendingReason));
        }

        // ── 5. Determine time window ──────────────────────────────────────────

        var endMs   = latestRows.Rows[^1].TimestampMs;
        var startMs = endMs - (long)(limit - 1) * tfInfo.StepMs;

        // ── 7. Build response ─────────────────────────────────────────────────

        return ServiceResult<ChartResponse>.Ok(
            await BuildChartResponseAsync(
                symbolUpper,
                timeframe,
                limit,
                tfInfo,
                ingestKey,
                startMs,
                endMs,
                latestRows,
                ct));
    }

    private async Task<ChartResponse> BuildChartResponseAsync(
        string symbol,
        string timeframe,
        int limit,
        TimeframeInfo tfInfo,
        string ingestKey,
        long startMs,
        long endMs,
        RowsFetchResult rowsResult,
        CancellationToken ct)
    {
        if (rowsResult.Rows.Count > 0)
        {
            var initialCoverage = (double)rowsResult.Rows.Count / limit;
            if (initialCoverage < _settings.FullCoverageThreshold)
            {
                _ = await TryTriggerWindowHydrationAsync(
                    ingestKey, symbol, tfInfo, limit, startMs, endMs, ct);
            }
        }

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

        // Cache only when data is complete; partial results get a shorter TTL
        var cacheTtl = CacheTtlFor(tfInfo, isFullCoverage);
        var cacheKey = BuildCacheKey(symbol, tfInfo, limit);
        await _cache.SetAsync(cacheKey, response, cacheTtl, ct);

        return response;
    }

    public async Task<ChartResponse?> TryGetCachedChartAsync(
        string symbol,
        string timeframe,
        int limit,
        CancellationToken ct = default)
    {
        if (!TimeframeMap.TryGetById(timeframe, out var tfInfo))
            return null;

        return await TryGetCachedChartAsync(symbol.ToUpperInvariant(), tfInfo, limit, ct);
    }

    public async Task<ChartResponse?> TryGetCachedChartAsync(
        string symbol,
        TimeframeInfo tfInfo,
        int limit,
        CancellationToken ct = default)
    {
        var exactKey = BuildCacheKey(symbol, tfInfo, limit);
        var exact = await _cache.GetAsync<ChartResponse>(exactKey, ct);
        if (exact is not null)
            return exact;

        foreach (var candidateLimit in CandleCountGrid.ForClass(tfInfo.Class).Where(value => value > limit))
        {
            var candidateKey = BuildCacheKey(symbol, tfInfo, candidateLimit);
            var candidate = await _cache.GetAsync<ChartResponse>(candidateKey, ct);
            if (!CanSatisfyFromCachedWindow(candidate, limit))
                continue;

            var sliced = SliceCachedWindow(candidate!, limit);
            await _cache.SetAsync(exactKey, sliced, CacheTtlFor(tfInfo, fullCoverage: true), ct);
            return sliced;
        }

        return null;
    }

    // ── Private helpers ───────────────────────────────────────────────────

    private static bool CanSatisfyFromCachedWindow(ChartResponse? candidate, int limit)
    {
        return candidate is not null
            && !string.Equals(candidate.Status, "pending", StringComparison.OrdinalIgnoreCase)
            && candidate.Candles.Count >= limit
            && candidate.Meta.Available >= limit;
    }

    private static ChartResponse SliceCachedWindow(ChartResponse source, int limit)
    {
        var candles = source.Candles.Count == limit
            ? source.Candles
            : source.Candles.Skip(source.Candles.Count - limit).ToArray();

        return new ChartResponse
        {
            Symbol = source.Symbol,
            Timeframe = source.Timeframe,
            Limit = limit,
            Candles = candles,
            Meta = new ChartMetaDto
            {
                Requested = limit,
                Available = candles.Count,
                FromMs = candles.Count > 0 ? candles[0].T : 0L,
                ToMs = candles.Count > 0 ? candles[^1].T : 0L,
                Coverage = candles.Count >= limit ? "full" : source.Meta.Coverage,
            },
            Status = candles.Count >= limit ? "ok" : source.Status,
            RetryAfterMs = candles.Count >= limit ? null : source.RetryAfterMs,
        };
    }

    private static ChartResponse BuildPendingResponse(
        string symbol, string timeframe, int limit, string reason)
    {
        return new ChartResponse
        {
            Symbol    = symbol,
            Timeframe = timeframe,
            Limit     = limit,
            Candles   = [],
            Meta = new ChartMetaDto
            {
                Requested = limit,
                Available = 0,
                Coverage  = "pending",
            },
            Status       = "pending",
            RetryAfterMs = 5_000,
        };
    }

    private ServiceResult<ChartResponse> BuildRowsFailureResult(
        string symbol,
        string timeframe,
        int limit,
        RowsFetchResult rowsResult,
        string operation)
    {
        var errorCode = string.IsNullOrWhiteSpace(rowsResult.ErrorCode)
            ? "DATA_SOURCE_UNAVAILABLE"
            : rowsResult.ErrorCode;
        var errorDetail = string.IsNullOrWhiteSpace(rowsResult.ErrorDetail)
            ? $"data-service {operation} failed for {symbol}/{timeframe} limit={limit}"
            : rowsResult.ErrorDetail;

        _log.LogWarning(
            "Chart request failed for {Symbol}/{Timeframe} limit={Limit}: {Code} {Detail}",
            symbol,
            timeframe,
            limit,
            errorCode,
            errorDetail);

        return ServiceResult<ChartResponse>.Fail($"{errorCode}: {errorDetail}");
    }

    private async Task<HydrationTriggerResult> TryTriggerWindowHydrationAsync(
        string ingestKey,
        string symbol,
        TimeframeInfo tfInfo,
        int limit,
        long startMs,
        long endMs,
        CancellationToken ct)
    {
        var lockAcquired = await _cache.SetIfNotExistsAsync(
            ingestKey, IngestInProgress,
            TimeSpan.FromSeconds(_settings.IngestLockTtlSeconds), ct);

        if (!lockAcquired)
        {
            _log.LogDebug("Background ingest already locked for {Symbol}/{Interval}",
                symbol, tfInfo.BybitInterval);
            return HydrationTriggerResult.AlreadyInProgress;
        }

        try
        {
            var correlationId = Activity.Current?.Id ?? "n/a";
            _log.LogInformation(
                "Scheduling chart window hydration for {Symbol}/{Interval} [{StartMs}..{EndMs}] "
                + "limit={Limit} correlationId={CorrelationId}",
                symbol, tfInfo.BybitInterval, startMs, endMs, limit, correlationId);

            TriggerIngestInBackground(ingestKey, symbol, tfInfo, limit, startMs, endMs);
            return HydrationTriggerResult.Started;
        }
        catch (Exception ex)
        {
            _log.LogError(ex,
                "Background ingest scheduling FAILED for {Symbol}/{Interval} [{StartMs}..{EndMs}]",
                symbol, tfInfo.BybitInterval, startMs, endMs);

            await _cache.SetAsync(
                ingestKey,
                IngestErrorCooldown,
                TimeSpan.FromSeconds(_settings.IngestErrorCooldownSeconds),
                CancellationToken.None);

            return HydrationTriggerResult.Failed;
        }
    }

    private string BuildCacheKey(string symbol, TimeframeInfo tfInfo, int limit)
    {
        return string.Format(ChartKeyFmt, symbol, tfInfo.BybitInterval, limit);
    }

    private void TriggerIngestInBackground(
        string ingestKey, string symbol, TimeframeInfo tfInfo, int limit, long startMs, long endMs)
    {
        var correlationId = Activity.Current?.Id ?? "n/a";

        _log.LogInformation(
            "Triggering background ingest for {Symbol}/{Interval} [{StartMs}..{EndMs}] "
            + "limit={Limit} correlationId={CorrelationId}",
            symbol, tfInfo.BybitInterval, startMs, endMs, limit, correlationId);

        _data.FireAndForgetIngest(
            symbol, tfInfo.BybitInterval, startMs, endMs,
            onComplete: () =>
            {
                _log.LogInformation(
                    "Background ingest completed for {Symbol}/{Interval} correlationId={CorrelationId}",
                    symbol, tfInfo.BybitInterval, correlationId);
                _ = _cache.RemoveAsync(ingestKey);
            },
            onError: ex =>
            {
                _log.LogError(ex,
                    "Background ingest FAILED for {Symbol}/{Interval} [{StartMs}..{EndMs}] "
                    + "correlationId={CorrelationId}",
                    symbol, tfInfo.BybitInterval, startMs, endMs, correlationId);

                _ = _cache.SetAsync(
                    ingestKey,
                    IngestErrorCooldown,
                    TimeSpan.FromSeconds(_settings.IngestErrorCooldownSeconds),
                    CancellationToken.None);
            });
    }

    private TimeSpan CacheTtlFor(TimeframeInfo tfInfo, bool fullCoverage)
    {
        // Partial/degraded results expire quickly so they get refreshed soon.
        if (!fullCoverage)
            return TimeSpan.FromSeconds(15);

        return tfInfo.Class switch
        {
            TimeframeClass.Heavy  => TimeSpan.FromSeconds(_settings.ChartCacheTtlHeavySeconds),
            TimeframeClass.Medium => TimeSpan.FromSeconds(_settings.ChartCacheTtlMediumSeconds),
            TimeframeClass.Light  => TimeSpan.FromSeconds(_settings.ChartCacheTtlLightSeconds),
            _                     => TimeSpan.FromSeconds(30),
        };
    }
}
