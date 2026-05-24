using System.Diagnostics;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public class ChartService : IChartService
{
    private enum SyncHydrationDisposition
    {
        RowsReady,
        KeepExistingRows,
        Pending,
        Failed,
    }

    private sealed record SyncHydrationResult(
        SyncHydrationDisposition Disposition,
        RowsFetchResult? Rows = null,
        string? PendingReason = null,
        string? Error = null);

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
            var hydrationResult = await TryHydrateWindowSynchronouslyAsync(
                ingestKey,
                symbolUpper,
                timeframe,
                tfInfo,
                limit,
                initialStartMs,
                initialEndMs,
                fallbackRows: null,
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
                        ct)),
                SyncHydrationDisposition.Pending => ServiceResult<ChartResponse>.Ok(
                    BuildPendingResponse(
                        symbolUpper,
                        timeframe,
                        limit,
                        hydrationResult.PendingReason ?? "Chart hydration is still in progress")),
                SyncHydrationDisposition.Failed => ServiceResult<ChartResponse>.Fail(
                    hydrationResult.Error ?? "SERVICE_BUSY: Unable to hydrate chart right now"),
                _ => ServiceResult<ChartResponse>.Ok(
                    BuildPendingResponse(
                        symbolUpper,
                        timeframe,
                        limit,
                        "Chart hydration is still in progress")),
            };
        }

        // ── 5. Determine time window ──────────────────────────────────────────

        var endMs   = latestRows.Rows[^1].TimestampMs;
        var startMs = endMs - (long)(limit - 1) * tfInfo.StepMs;
        var rowsForResponse = latestRows;

        if (NeedsHydration(rowsForResponse, limit))
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
                ct));
    }

    private async Task<ChartResponse> BuildChartResponseAsync(
        string symbol,
        string timeframe,
        int limit,
        TimeframeInfo tfInfo,
        RowsFetchResult rowsResult,
        CancellationToken ct)
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

    private bool NeedsHydration(RowsFetchResult rowsResult, int limit)
    {
        return rowsResult.HasRows && (double)rowsResult.Rows.Count / limit < _settings.FullCoverageThreshold;
    }

    private async Task<SyncHydrationResult> TryHydrateWindowSynchronouslyAsync(
        string ingestKey,
        string symbol,
        string timeframe,
        TimeframeInfo tfInfo,
        int limit,
        long startMs,
        long endMs,
        RowsFetchResult? fallbackRows,
        CancellationToken ct)
    {
        var lockAcquired = await _cache.SetIfNotExistsAsync(
            ingestKey,
            IngestInProgress,
            TimeSpan.FromSeconds(_settings.IngestLockTtlSeconds),
            ct);

        if (!lockAcquired)
        {
            _log.LogDebug(
                "Chart hydration already in progress for {Symbol}/{Interval}",
                symbol,
                tfInfo.BybitInterval);

            return fallbackRows is { HasRows: true }
                ? new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows)
                : new SyncHydrationResult(
                    SyncHydrationDisposition.Pending,
                    PendingReason: "Ingest already in progress for this symbol/timeframe");
        }

        var keepLock = false;
        var setErrorCooldown = false;

        try
        {
            var correlationId = Activity.Current?.Id ?? "n/a";
            _log.LogInformation(
                "Running synchronous chart hydration for {Symbol}/{Interval} [{StartMs}..{EndMs}] limit={Limit} correlationId={CorrelationId}",
                symbol,
                tfInfo.BybitInterval,
                startMs,
                endMs,
                limit,
                correlationId);

            var ingestResult = await _data.IngestAsync(
                symbol,
                tfInfo.BybitInterval,
                startMs,
                endMs,
                ct);

            if (ingestResult.IsInProgress)
            {
                keepLock = true;

                return fallbackRows is { HasRows: true }
                    ? new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows)
                    : new SyncHydrationResult(
                        SyncHydrationDisposition.Pending,
                        PendingReason: BuildHydrationPendingReason(ingestResult));
            }

            if (ingestResult.IsFailure)
            {
                setErrorCooldown = true;
                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: BuildIngestFailureError(symbol, timeframe, limit, ingestResult));
            }

            var tableName = string.IsNullOrWhiteSpace(ingestResult.TableName)
                ? BuildTableName(symbol, tfInfo.BybitInterval)
                : ingestResult.TableName;
            var hydratedRows = await _data.GetRowsAsync(tableName, startMs, endMs, limit, ct);

            if (hydratedRows.IsFailure)
            {
                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: BuildRowsFailureError(symbol, timeframe, limit, hydratedRows, "rows"));
            }

            if (hydratedRows.IsClaimCheck)
            {
                return fallbackRows is { HasRows: true }
                    ? new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows)
                    : new SyncHydrationResult(
                        SyncHydrationDisposition.Pending,
                        PendingReason: "Data payload too large; use a smaller limit or wait for streaming path");
            }

            if (hydratedRows.IsEmpty)
            {
                return fallbackRows is { HasRows: true }
                    ? new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows)
                    : new SyncHydrationResult(
                        SyncHydrationDisposition.Pending,
                        PendingReason: "Chart hydration completed but candles are not visible yet");
            }

            if (NeedsHydration(hydratedRows, limit))
            {
                var refreshedRows = await _data.GetRowsAsync(tableName, startMs, endMs, limit, ct);

                if (refreshedRows.IsFailure)
                {
                    return new SyncHydrationResult(
                        SyncHydrationDisposition.Failed,
                        Error: BuildRowsFailureError(symbol, timeframe, limit, refreshedRows, "rows"));
                }

                if (refreshedRows.HasRows)
                {
                    hydratedRows = refreshedRows;
                }
            }

            return new SyncHydrationResult(SyncHydrationDisposition.RowsReady, hydratedRows);
        }
        catch (Exception ex)
        {
            setErrorCooldown = true;
            _log.LogError(ex,
                "Synchronous chart hydration FAILED for {Symbol}/{Interval} [{StartMs}..{EndMs}]",
                symbol,
                tfInfo.BybitInterval,
                startMs,
                endMs);
            return new SyncHydrationResult(
                SyncHydrationDisposition.Failed,
                Error: "SERVICE_BUSY: Unable to hydrate chart right now");
        }
        finally
        {
            if (keepLock)
            {
            }
            else if (setErrorCooldown)
            {
                await _cache.SetAsync(
                    ingestKey,
                    IngestErrorCooldown,
                    TimeSpan.FromSeconds(_settings.IngestErrorCooldownSeconds),
                    CancellationToken.None);
            }
            else
            {
                await _cache.RemoveAsync(ingestKey);
            }
        }
    }

    private ServiceResult<ChartResponse> BuildRowsFailureResult(
        string symbol,
        string timeframe,
        int limit,
        RowsFetchResult rowsResult,
        string operation)
    {
        return ServiceResult<ChartResponse>.Fail(
            BuildRowsFailureError(symbol, timeframe, limit, rowsResult, operation));
    }

    private string BuildRowsFailureError(
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

        return $"{errorCode}: {errorDetail}";
    }

    private string BuildIngestFailureError(
        string symbol,
        string timeframe,
        int limit,
        IngestResult ingestResult)
    {
        var errorCode = string.IsNullOrWhiteSpace(ingestResult.ErrorCode)
            ? "SERVICE_BUSY"
            : ingestResult.ErrorCode;
        var errorDetail = string.IsNullOrWhiteSpace(ingestResult.ErrorDetail)
            ? ingestResult.Error ?? $"chart hydration failed for {symbol}/{timeframe} limit={limit}"
            : ingestResult.ErrorDetail;

        _log.LogWarning(
            "Chart hydration failed for {Symbol}/{Timeframe} limit={Limit}: {Code} {Detail}",
            symbol,
            timeframe,
            limit,
            errorCode,
            errorDetail);

        return $"{errorCode}: {errorDetail}";
    }

    private static string BuildHydrationPendingReason(IngestResult ingestResult)
    {
        return string.IsNullOrWhiteSpace(ingestResult.ErrorDetail)
            ? "Chart hydration is still in progress"
            : ingestResult.ErrorDetail!;
    }

    private static string BuildTableName(string symbol, string bybitInterval)
    {
        return $"{symbol.ToLowerInvariant()}_{bybitInterval}";
    }

    private string BuildCacheKey(string symbol, TimeframeInfo tfInfo, int limit)
    {
        return string.Format(ChartKeyFmt, symbol, tfInfo.BybitInterval, limit);
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
