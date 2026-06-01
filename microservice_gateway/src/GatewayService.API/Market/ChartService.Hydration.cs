using System.Diagnostics;
using GatewayService.API.Common;

namespace GatewayService.API.Market;

public partial class ChartService
{
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

    /// <summary>
    /// Returns true when the freshest candle in the result is older than
    /// 2 × stepMs from "now". Used as a freshness gate so a request like
    /// "give me the last 200 hourly candles" never silently returns a window
    /// that ends 2 days ago when the table has not been refreshed (e.g. when
    /// the watcher dropped a websocket or hasn't been subscribed to a given
    /// exchange yet). Caller should treat a stale window the same as a
    /// partial window and trigger a synchronous hydration step.
    /// </summary>
    private static bool IsLatestRowStale(RowsFetchResult rowsResult, TimeframeInfo tfInfo)
    {
        if (!rowsResult.HasRows) return false;
        var latestTs = rowsResult.Rows[^1].TimestampMs;
        var ageMs    = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - latestTs;
        return ageMs > 2L * tfInfo.StepMs;
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
}
