using System.Diagnostics;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public class ChartService : IChartService
{
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

        var cacheKey = string.Format(ChartKeyFmt, symbolUpper, tfInfo.BybitInterval, limit);
        var cached   = await _cache.GetAsync<ChartResponse>(cacheKey, ct);
        if (cached is not null)
            return ServiceResult<ChartResponse>.Ok(cached);

        // ── 3. Check ingest lock ──────────────────────────────────────────────
        // "inprogress"     → another request already triggered ingest, wait for it
        // "error_cooldown" → previous ingest failed; short retry window before re-attempt
        // null             → no ingest in flight, proceed normally

        var ingestKey    = string.Format(IngestLockFmt, symbolUpper, tfInfo.BybitInterval);
        var ingestActive = await _cache.GetAsync<string>(ingestKey, ct);
        if (ingestActive is not null)
        {
            var hint = ingestActive == IngestErrorCooldown
                ? "Previous ingest attempt failed; a retry will happen after the cooldown"
                : "Ingest already in progress for this symbol/timeframe";
            return ServiceResult<ChartResponse>.Ok(
                BuildPendingResponse(symbolUpper, timeframe, limit, hint));
        }

        // ── 4. Query data-service coverage ───────────────────────────────────

        var coverage = await _data.GetCoverageAsync(symbolUpper, tfInfo.BybitInterval, ct);

        if (coverage is null || !coverage.Exists || coverage.Rows == 0)
        {
            await TriggerIngestAsync(ingestKey, symbolUpper, tfInfo, limit, ct);
            return ServiceResult<ChartResponse>.Ok(BuildPendingResponse(
                symbolUpper, timeframe, limit, "No data available; ingest triggered"));
        }

        // ── 5. Determine time window ──────────────────────────────────────────

        var endMs   = coverage.MaxTsMs;
        var startMs = endMs - (long)(limit - 1) * tfInfo.StepMs;

        // ── 6. Fetch rows (with limit so the data-service caps its response size) ──

        var rowsResult = await _data.GetRowsAsync(
            coverage.TableName, startMs, endMs, limit, ct);

        // Claim-check: data IS in the data-service but the Kafka response was too large.
        // Do NOT trigger a new ingest — the data exists, just not fetchable at this size.
        if (rowsResult.IsClaimCheck)
        {
            _log.LogWarning(
                "Claim-check for {Symbol}/{Interval} (table={Table}, limit={Limit}) — "
                + "payload too large for Kafka; client should use a smaller limit",
                symbolUpper, tfInfo.BybitInterval, coverage.TableName, limit);
            return ServiceResult<ChartResponse>.Ok(
                BuildPendingResponse(symbolUpper, timeframe, limit,
                    "Data payload too large; use a smaller limit or wait for streaming path"));
        }

        if (rowsResult.Rows.Count == 0)
        {
            await TriggerIngestAsync(ingestKey, symbolUpper, tfInfo, limit, ct);
            return ServiceResult<ChartResponse>.Ok(
                BuildPendingResponse(symbolUpper, timeframe, limit,
                    "No rows returned; ingest triggered"));
        }

        // ── 7. Build response ─────────────────────────────────────────────────

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

        if (isFullCoverage)
        {
            status = "ok";
        }
        else
        {
            status       = "partial";
            retryAfterMs = _settings.IngestRetryAfterMs;

            // Trigger background ingest to fill the gap
            var ingestLockAcquired = await _cache.SetIfNotExistsAsync(
                ingestKey, IngestInProgress,
                TimeSpan.FromSeconds(_settings.IngestLockTtlSeconds), ct);

            if (ingestLockAcquired)
                TriggerIngestInBackground(
                    ingestKey, symbolUpper, tfInfo, limit, startMs, endMs);
        }

        var coverageLabel = isFullCoverage ? "full" : "partial";

        var response = new ChartResponse
        {
            Symbol    = symbolUpper,
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
        await _cache.SetAsync(cacheKey, response, cacheTtl, ct);

        return ServiceResult<ChartResponse>.Ok(response);
    }

    // ── Private helpers ───────────────────────────────────────────────────

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

    /// <summary>Acquires the ingest lock and starts fire-and-forget ingest.</summary>
    private async Task TriggerIngestAsync(
        string ingestKey, string symbol, TimeframeInfo tfInfo, int limit, CancellationToken ct)
    {
        var lockAcquired = await _cache.SetIfNotExistsAsync(
            ingestKey, IngestInProgress,
            TimeSpan.FromSeconds(_settings.IngestLockTtlSeconds), ct);

        if (!lockAcquired)
        {
            _log.LogDebug("Ingest already locked for {Symbol}/{Interval}",
                symbol, tfInfo.BybitInterval);
            return;
        }

        var nowMs    = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var windowMs = (long)limit * tfInfo.StepMs * _settings.IngestWindowMultiplier;
        var startMs  = nowMs - windowMs;
        var endMs    = nowMs;

        TriggerIngestInBackground(ingestKey, symbol, tfInfo, limit, startMs, endMs);
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

                // Replace the in-progress lock with a short error-cooldown so that the
                // next client request retries quickly rather than waiting the full IngestLockTtlSeconds.
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
