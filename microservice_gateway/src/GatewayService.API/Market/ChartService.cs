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
        if (exchangeKey == "bybit" && !await _config.IsKnownSymbolAsync(symbolUpper, ct))
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
        var cacheKey = BuildCacheKey(symbol, tfInfo, limit, exchange);
        await _cache.SetAsync(cacheKey, response, cacheTtl, ct);

        return response;
    }

    public async Task<ChartResponse?> TryGetCachedChartAsync(
        string symbol,
        string timeframe,
        int limit,
        string exchange = "bybit",
        CancellationToken ct = default)
    {
        if (!TimeframeMap.TryGetById(timeframe, out var tfInfo))
            return null;

        return await TryGetCachedChartAsync(
            symbol.ToUpperInvariant(),
            tfInfo,
            limit,
            DataServiceClient.NormalizeExchange(exchange),
            ct);
    }

    public async Task<ChartResponse?> TryGetCachedChartAsync(
        string symbol,
        TimeframeInfo tfInfo,
        int limit,
        string exchange,
        CancellationToken ct = default)
    {
        var exactKey = BuildCacheKey(symbol, tfInfo, limit, exchange);
        var exact = await _cache.GetAsync<ChartResponse>(exactKey, ct);
        if (exact is not null)
            return exact;

        foreach (var candidateLimit in CandleCountGrid.ForClass(tfInfo.Class).Where(value => value > limit))
        {
            var candidateKey = BuildCacheKey(symbol, tfInfo, candidateLimit, exchange);
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

    /// <summary>
    /// Polls the latest fixed-width window while another in-flight ingest
    /// completes. Returns the first non-empty / failure result, or an Empty
    /// result if the budget expires before rows show up.
    /// Honours the per-attempt cancellation token and respects the
    /// "error_cooldown" lock value as an early exit (no rows will arrive).
    /// </summary>
    private async Task<RowsFetchResult> WaitForInflightIngestAsync(
        string ingestKey,
        string symbol,
        TimeframeInfo tfInfo,
        int limit,
        string exchange,
        CancellationToken ct)
    {
        var maxWaitSeconds = Math.Max(1, _settings.ChartInflightWaitSeconds);
        var pollIntervalMs = Math.Max(50, _settings.ChartInflightPollMs);
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(maxWaitSeconds);
        var attempt = 0;

        while (!ct.IsCancellationRequested && DateTime.UtcNow < deadline)
        {
            attempt++;
            await Task.Delay(pollIntervalMs, ct);

            // If the holder marked the lock as failed, stop waiting early.
            var lockValue = await _cache.GetAsync<string>(ingestKey, ct);
            if (lockValue == IngestErrorCooldown)
            {
                return RowsFetchResult.Fail(
                    "SERVICE_BUSY",
                    "Previous ingest attempt failed; retry after the cooldown window");
            }

            var rows = await _data.GetLatestWindowRowsAsync(
                symbol,
                tfInfo.BybitInterval,
                tfInfo.StepMs,
                limit,
                DataServiceClient.ChartProjectionColumns,
                exchange,
                ct);

            if (rows.IsFailure)
            {
                return rows;
            }

            if (rows.HasRows)
            {
                _log.LogDebug(
                    "Inflight chart ingest produced rows for {Symbol}/{Interval} after {Attempts} polls",
                    symbol, tfInfo.BybitInterval, attempt);
                return rows;
            }

            // ClaimCheck or Empty → keep polling until budget runs out, with
            // the ClaimCheck case finally bubbling up only if it never resolves.
            if (rows.IsClaimCheck)
            {
                return RowsFetchResult.Fail(
                    "DATA_SOURCE_UNAVAILABLE",
                    "Chart payload exceeds Kafka size limit; use a smaller limit");
            }

            // Lock disappeared and we still have no rows → the holder either
            // succeeded with a different window or gave up. Try one more
            // poll cycle to be safe, then exit.
            if (lockValue is null && attempt > 1)
            {
                break;
            }
        }

        return RowsFetchResult.Empty;
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
        string exchange,
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

            if (fallbackRows is { HasRows: true })
            {
                return new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows);
            }

            // Another caller already owns the ingest lock — wait for rows
            // synchronously rather than telling the client to come back later.
            var waitedRows = await WaitForInflightIngestAsync(
                ingestKey, symbol, tfInfo, limit, exchange, ct);

            if (waitedRows.IsFailure)
            {
                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: BuildRowsFailureError(symbol, timeframe, limit, waitedRows, "latest_rows"));
            }

            if (waitedRows.HasRows)
            {
                return new SyncHydrationResult(SyncHydrationDisposition.RowsReady, waitedRows);
            }

            return new SyncHydrationResult(
                SyncHydrationDisposition.Failed,
                Error: "SERVICE_BUSY: Chart ingest is still running for this symbol/timeframe; try again shortly");
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
                exchange,
                ct);

            if (ingestResult.IsInProgress)
            {
                // The data-service kicked off an async ingest job and the
                // Kafka request returned before it completed. Keep our
                // ingest-lock alive (so concurrent callers wait) and poll
                // the latest window until either rows show up or we run
                // out of budget. We never return "pending" to the client.
                keepLock = true;

                if (fallbackRows is { HasRows: true })
                {
                    return new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows);
                }

                var waitedRows = await WaitForInflightIngestAsync(
                    ingestKey, symbol, tfInfo, limit, exchange, ct);

                if (waitedRows.IsFailure)
                {
                    return new SyncHydrationResult(
                        SyncHydrationDisposition.Failed,
                        Error: BuildRowsFailureError(symbol, timeframe, limit, waitedRows, "latest_rows"));
                }

                if (waitedRows.HasRows)
                {
                    return new SyncHydrationResult(SyncHydrationDisposition.RowsReady, waitedRows);
                }

                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: $"SERVICE_BUSY: {BuildHydrationPendingReason(ingestResult)}");
            }

            if (ingestResult.IsFailure)
            {
                setErrorCooldown = true;
                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: BuildIngestFailureError(symbol, timeframe, limit, ingestResult));
            }

            var tableName = string.IsNullOrWhiteSpace(ingestResult.TableName)
                ? BuildTableName(symbol, tfInfo.BybitInterval, exchange)
                : ingestResult.TableName;
            var hydratedRows = await _data.GetRowsAsync(
                tableName, startMs, endMs, limit,
                DataServiceClient.ChartProjectionColumns, ct);

            if (hydratedRows.IsFailure)
            {
                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: BuildRowsFailureError(symbol, timeframe, limit, hydratedRows, "rows"));
            }

            if (hydratedRows.IsClaimCheck)
            {
                if (fallbackRows is { HasRows: true })
                {
                    return new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows);
                }

                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: "DATA_SOURCE_UNAVAILABLE: Chart payload exceeds Kafka size limit; use a smaller limit");
            }

            if (hydratedRows.IsEmpty)
            {
                if (fallbackRows is { HasRows: true })
                {
                    return new SyncHydrationResult(SyncHydrationDisposition.KeepExistingRows, fallbackRows);
                }

                // Ingest call succeeded but the window we asked for is still
                // empty — give the data-service a few more polling cycles
                // before deciding it's a real failure.
                var waitedRows = await WaitForInflightIngestAsync(
                    ingestKey, symbol, tfInfo, limit, exchange, ct);

                if (waitedRows.IsFailure)
                {
                    return new SyncHydrationResult(
                        SyncHydrationDisposition.Failed,
                        Error: BuildRowsFailureError(symbol, timeframe, limit, waitedRows, "latest_rows"));
                }

                if (waitedRows.HasRows)
                {
                    return new SyncHydrationResult(SyncHydrationDisposition.RowsReady, waitedRows);
                }

                return new SyncHydrationResult(
                    SyncHydrationDisposition.Failed,
                    Error: "SERVICE_BUSY: Chart hydration completed but candles are not visible yet; try again shortly");
            }

            if (NeedsHydration(hydratedRows, limit))
            {
                var refreshedRows = await _data.GetRowsAsync(
                    tableName, startMs, endMs, limit,
                    DataServiceClient.ChartProjectionColumns, ct);

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

    // Data-service tables use the canonical client timeframe key
    // ("60m", "1d"), not the Bybit kline interval ("60", "D"). Sending
    // the Bybit value here would point us at non-existent tables and
    // surface as 42P01 in data-service logs / 503 rows-timeout to clients.
    // For non-Bybit exchanges DatasetCore prefixes the exchange ("binance_btcusdt_60m").
    private static string BuildTableName(string symbol, string bybitInterval, string exchange)
    {
        return DataServiceClient.BuildTableName(symbol, bybitInterval, exchange);
    }

    private string BuildCacheKey(string symbol, TimeframeInfo tfInfo, int limit, string exchange)
    {
        return string.Format(ChartKeyFmt, exchange, symbol, tfInfo.BybitInterval, limit);
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
