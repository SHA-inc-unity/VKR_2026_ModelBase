using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

public partial class ChartService
{
    // Cursor-scoped page cache. A historical page (everything strictly older
    // than a given candle) never changes, so it is cached under a key that
    // includes the cursor and kept for a long TTL once fully covered.
    //   market:chart:page:{exchange}:{symbol}:{bybitInterval}:{limit}:{beforeMs}:v1
    private const string PageKeyFmt = "market:chart:page:{0}:{1}:{2}:{3}:{4}:v1";

    /// <summary>
    /// Returns the page of <paramref name="limit"/> candles immediately OLDER
    /// than the <paramref name="beforeMs"/> cursor (exclusive) — the building
    /// block for infinite left-panning. The data-service is queried by time
    /// range; if the requested window is missing or sparse it is backfilled
    /// from the exchange on demand (a bounded, lock-guarded synchronous ingest
    /// that never falls back to the *latest* window, so the page is always the
    /// correct historical slice). An empty page means we reached the start of
    /// available history (the client stops paginating); a transient failure is
    /// surfaced as a real error so the client keeps trying.
    /// </summary>
    public virtual async Task<ServiceResult<ChartResponse>> GetChartBeforeAsync(
        string symbol, string timeframe, int limit, long beforeMs,
        string exchange = "bybit", CancellationToken ct = default)
    {
        var symbolUpper = symbol.ToUpperInvariant();
        var exchangeKey = DataServiceClient.NormalizeExchange(exchange);

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

        if (beforeMs <= 0)
            return ServiceResult<ChartResponse>.Fail(
                "INVALID_CURSOR: 'before' must be a positive unix-millisecond timestamp");

        // Exactly `limit` candle slots ending strictly before the cursor.
        var endMs   = beforeMs - 1;
        var startMs = endMs - (long)(limit - 1) * tfInfo.StepMs;

        var pageKey = string.Format(
            PageKeyFmt, exchangeKey, symbolUpper, tfInfo.BybitInterval, limit, beforeMs);
        var cachedPage = await _cache.GetAsync<ChartResponse>(pageKey, ct);
        if (cachedPage is not null)
            return ServiceResult<ChartResponse>.Ok(cachedPage);

        var tableName = BuildTableName(symbolUpper, tfInfo.BybitInterval, exchangeKey);
        var rows = await _data.GetRowsAsync(
            tableName, startMs, endMs, limit,
            DataServiceClient.ChartProjectionColumns, ct);

        if (rows.IsFailure)
            return BuildRowsFailureResult(symbolUpper, timeframe, limit, rows, "rows");

        if (rows.IsClaimCheck)
            return ServiceResult<ChartResponse>.Fail(
                "DATA_SOURCE_UNAVAILABLE: Chart payload exceeds Kafka size limit; use a smaller limit");

        // Missing or sparse window → backfill that exact range from the exchange.
        if (rows.IsEmpty || NeedsHydration(rows, limit))
        {
            rows = await HydrateHistoricalPageAsync(
                symbolUpper, tfInfo, limit, startMs, endMs, exchangeKey, rows, ct);

            if (rows.IsFailure)
                return BuildRowsFailureResult(symbolUpper, timeframe, limit, rows, "rows");
        }

        // An empty page is a valid "ok" response carrying zero candles — the
        // client reads it as "no more history" and stops paginating.
        var response = await BuildChartResponseAsync(
            symbolUpper, timeframe, limit, tfInfo, rows, exchangeKey, ct,
            cacheKeyOverride: pageKey,
            cacheTtlOverride: PageCacheTtlFor(rows, limit));

        return ServiceResult<ChartResponse>.Ok(response);
    }

    /// <summary>
    /// Backfills the exact [startMs, endMs] historical window from the exchange
    /// and re-reads it. Unlike the general hydration path this NEVER polls the
    /// latest window (which would return the wrong candles for a historical
    /// page). Lock-guarded so concurrent requests don't double-ingest.
    /// Returns:
    ///  • fresh rows when the backfill produced data,
    ///  • the (possibly empty) existing rows when the exchange has nothing
    ///    older (genuine start-of-history),
    ///  • a Fail result on a transient condition (lock contended / ingest
    ///    in-flight / failure) with no rows to show, so the caller surfaces a
    ///    503 and the client keeps paginating instead of stopping.
    /// </summary>
    private async Task<RowsFetchResult> HydrateHistoricalPageAsync(
        string symbol, TimeframeInfo tfInfo, int limit,
        long startMs, long endMs, string exchange, RowsFetchResult existing,
        CancellationToken ct)
    {
        var ingestKey = string.Format(IngestLockFmt, exchange, symbol, tfInfo.BybitInterval);
        var lockAcquired = await _cache.SetIfNotExistsAsync(
            ingestKey, IngestInProgress, TimeSpan.FromSeconds(_settings.IngestLockTtlSeconds), ct);

        if (!lockAcquired)
        {
            return existing.HasRows
                ? existing
                : RowsFetchResult.Fail("SERVICE_BUSY", "another ingest is in progress for this symbol/timeframe");
        }

        try
        {
            var ingest = await _data.IngestAsync(symbol, tfInfo.BybitInterval, startMs, endMs, exchange, ct);

            if (ingest.IsInProgress)
            {
                return existing.HasRows
                    ? existing
                    : RowsFetchResult.Fail("SERVICE_BUSY", "historical backfill is still running; try again shortly");
            }

            if (ingest.IsFailure)
            {
                return existing.HasRows
                    ? existing
                    : RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", "historical backfill failed");
            }

            var tableName = string.IsNullOrWhiteSpace(ingest.TableName)
                ? BuildTableName(symbol, tfInfo.BybitInterval, exchange)
                : ingest.TableName;

            var fresh = await _data.GetRowsAsync(
                tableName, startMs, endMs, limit,
                DataServiceClient.ChartProjectionColumns, ct);

            if (fresh.IsFailure)
                return existing.HasRows ? existing : fresh;

            // Fresh has data → use it. Fresh empty + ingest succeeded → the
            // exchange genuinely has nothing older → return existing (empty).
            return fresh.HasRows ? fresh : existing;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Historical page hydrate failed for {Exchange}/{Symbol}/{Interval} [{Start}..{End}]",
                exchange, symbol, tfInfo.BybitInterval, startMs, endMs);
            return existing.HasRows
                ? existing
                : RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", $"historical backfill error: {ex.Message}");
        }
        finally
        {
            await _cache.RemoveAsync(ingestKey, ct);
        }
    }

    /// <summary>
    /// A fully-covered historical page is immutable → cache it for hours.
    /// An empty/partial page gets a short TTL so a later backfill (or the
    /// MarketWatcher catching up) can fill it on the next request.
    /// </summary>
    private TimeSpan PageCacheTtlFor(RowsFetchResult rows, int limit)
    {
        var coverage = rows.HasRows ? (double)rows.Rows.Count / limit : 0.0;
        return coverage >= _settings.FullCoverageThreshold
            ? TimeSpan.FromHours(6)
            : TimeSpan.FromSeconds(20);
    }
}
