using System.Diagnostics;
using System.Text.Json;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

public sealed partial class DataServiceClient
{
    /// <inheritdoc />
    public async Task<IngestResult> IngestAsync(
        string symbol, string bybitInterval, long startMs, long endMs,
        string exchange = "bybit", CancellationToken ct = default)
    {
        var totalTimeout = TimeSpan.FromSeconds(_settings.IngestKafkaTimeoutSeconds);
        var requestTimeout = TimeSpan.FromSeconds(Math.Min(
            _settings.KafkaTimeoutSeconds,
            _settings.IngestKafkaTimeoutSeconds));
        var tableName = BuildTableName(symbol, bybitInterval, exchange);
        // Data-service expects the canonical client timeframe key ("60m"),
        // not the Bybit interval ("60"). Sending the raw Bybit value made
        // data-service create/look up the wrong table name and triggered
        // 42P01 "relation does not exist" on the rows-read path.
        var timeframeKey = TimeframeMap.BybitIntervalToClientId(bybitInterval);
        var exchangeKey = NormalizeExchange(exchange);

        try
        {
            var startReply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetJobsStart,
                new
                {
                    type = "ingest",
                    target_table = tableName,
                    target_symbol = symbol,
                    target_timeframe = timeframeKey,
                    target_exchange = exchangeKey,
                    target_start_ms = startMs,
                    target_end_ms = endMs,
                    created_by = "gateway_market_chart",
                    @params = new
                    {
                        symbol,
                        timeframe = timeframeKey,
                        exchange = exchangeKey,
                        start_ms = startMs,
                        end_ms = endMs,
                        // Chart endpoint only consumes raw OHLCV columns
                        // (see ChartProjectionColumns). Telling the
                        // IngestJobHandler to skip the expensive
                        // ComputeAndUpdateFeaturesSinceAsync window-aggregation
                        // makes cold-table chart requests 2-10x faster.
                        // The full feature pipeline still runs for non-chart
                        // ingests (admin UI, scheduled jobs, market_watcher).
                        skip_features = true,
                    },
                },
                requestTimeout,
                ct);

            if (TryGetReplyError(startReply, out var startError))
            {
                var errorCode = NormalizeIngestErrorCode(startError.Code, "SERVICE_BUSY");
                var errorDetail = BuildReplyErrorDetail(startError);
                _log.LogError(
                    "Ingest job start failed for {Symbol}/{Interval}@{Exchange}: code={Code} detail={Detail}",
                    symbol, bybitInterval, exchangeKey, errorCode, errorDetail);
                return IngestResult.FailWithCode(errorCode, errorDetail, tableName);
            }

            var jobId = TryGetString(startReply, "job_id");
            if (string.IsNullOrWhiteSpace(jobId) &&
                TryGetNestedJob(startReply, out var startJob))
            {
                jobId = TryGetString(startJob, "job_id");
            }

            if (string.IsNullOrWhiteSpace(jobId))
            {
                _log.LogError(
                    "Ingest job start returned no job_id for {Symbol}/{Interval}@{Exchange}",
                    symbol, bybitInterval, exchangeKey);
                return IngestResult.FailWithCode(
                    "DATA_SOURCE_UNAVAILABLE",
                    "ingest_job_id_missing",
                    tableName);
            }

            var deduped = startReply.ValueKind == JsonValueKind.Object &&
                startReply.TryGetProperty("deduped", out var dedupedEl) &&
                dedupedEl.ValueKind == JsonValueKind.True;

            _log.LogInformation(
                "Ingest job {JobId} {Mode} for {Symbol}/{Interval}@{Exchange} [{StartMs}..{EndMs}]",
                jobId,
                deduped ? "reused" : "started",
                symbol,
                bybitInterval,
                exchangeKey,
                startMs,
                endMs);

            return await WaitForIngestJobAsync(jobId, tableName, totalTimeout, requestTimeout, ct);
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Queued ingest start timed out for {Symbol}/{Interval}@{Exchange}",
                symbol, bybitInterval, exchangeKey);
            return IngestResult.FailWithCode(
                "DOWNSTREAM_TIMEOUT",
                $"ingest start timed out for {tableName} [{startMs}..{endMs}]",
                tableName);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Ingest Kafka request failed for {Symbol}/{Interval}@{Exchange}",
                symbol, bybitInterval, exchangeKey);
            return IngestResult.FailWithCode(
                "DATA_SOURCE_UNAVAILABLE",
                $"ingest failed for {tableName} [{startMs}..{endMs}]: {ex.Message}",
                tableName);
        }
    }

    /// <inheritdoc />
    public void FireAndForgetIngest(
        string symbol, string bybitInterval, long startMs, long endMs,
        Action onComplete, Action<Exception> onError, string exchange = "bybit")
    {
        _ = Task.Run(async () =>
        {
            try
            {
                var result = await IngestAsync(
                    symbol,
                    bybitInterval,
                    startMs,
                    endMs,
                    exchange,
                    CancellationToken.None);

                if (!result.Success)
                {
                    _log.LogError(
                        "Background queued ingest failed for {Symbol}/{Interval}@{Exchange}: {Error}",
                        symbol, bybitInterval, exchange, result.Error ?? "unknown error");
                    onError(new InvalidOperationException(result.Error ?? "unknown error"));
                }
                else
                {
                    _log.LogInformation(
                        "Background queued ingest completed for {Symbol}/{Interval}@{Exchange}: {Rows} rows ingested",
                        symbol, bybitInterval, exchange, result.RowsIngested);
                    onComplete();
                }
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Background ingest task failed for {Symbol}/{Interval}@{Exchange}",
                    symbol, bybitInterval, exchange);
                onError(ex);
            }
        });
    }

    private async Task<IngestResult> WaitForIngestJobAsync(
        string jobId,
        string fallbackTableName,
        TimeSpan totalTimeout,
        TimeSpan requestTimeout,
        CancellationToken ct)
    {
        var stopwatch = Stopwatch.StartNew();

        while (stopwatch.Elapsed < totalTimeout)
        {
            var remaining = totalTimeout - stopwatch.Elapsed;
            var effectiveTimeout = remaining < requestTimeout ? remaining : requestTimeout;

            JsonElement jobReply;
            try
            {
                // Bound the server-side wait by our remaining budget so we
                // do not block longer than the caller is willing to wait.
                var serverWaitMs = Math.Min(
                    JobsGetServerWaitMs,
                    Math.Max(0, (int)remaining.TotalMilliseconds - 250));
                jobReply = await _kafka.RequestAsync(
                    DataTopics.CmdDataDatasetJobsGet,
                    new { job_id = jobId, wait_terminal_ms = serverWaitMs },
                    effectiveTimeout,
                    ct);
            }
            catch (TimeoutException ex)
            {
                _log.LogWarning(ex,
                    "Queued ingest job {JobId} status polling timed out",
                    jobId);
                return IngestResult.InProgress(
                    fallbackTableName,
                    errorDetail: $"ingest job {jobId} is still running");
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex,
                    "Queued ingest job {JobId} status polling failed",
                    jobId);
                return IngestResult.FailWithCode(
                    "DATA_SOURCE_UNAVAILABLE",
                    $"ingest status polling failed for job {jobId}: {ex.Message}",
                    fallbackTableName);
            }

            if (TryGetReplyError(jobReply, out var jobReplyError))
            {
                var errorCode = NormalizeIngestErrorCode(jobReplyError.Code, "DATA_SOURCE_UNAVAILABLE");
                var errorDetail = BuildReplyErrorDetail(jobReplyError);
                _log.LogError(
                    "Queued ingest job {JobId} failed to load status: code={Code} detail={Detail}",
                    jobId, errorCode, errorDetail);
                return IngestResult.FailWithCode(errorCode, errorDetail, fallbackTableName);
            }

            if (!TryGetNestedJob(jobReply, out var job))
            {
                _log.LogError(
                    "Queued ingest job {JobId} returned invalid payload",
                    jobId);
                return IngestResult.FailWithCode(
                    "DATA_SOURCE_UNAVAILABLE",
                    "ingest_job_payload_invalid",
                    fallbackTableName);
            }

            var status = TryGetString(job, "status") ?? string.Empty;
            var tableName = TryGetString(job, "target_table") ?? fallbackTableName;
            var completed = ClampToInt(GetLong(job, "completed"));

            switch (status)
            {
                case "succeeded":
                case "skipped":
                    _log.LogInformation(
                        "Queued ingest job {JobId} completed with status={Status} table={Table} completed={Completed}",
                        jobId, status, tableName, completed);
                    return IngestResult.Ok(tableName, completed);

                case "failed":
                case "canceled":
                {
                    var error = BuildJobFailure(job, status);
                    var errorCode = NormalizeIngestErrorCode(
                        TryGetString(job, "error_code"),
                        "SERVICE_BUSY");
                    _log.LogWarning(
                        "Queued ingest job {JobId} finished with status={Status}: {Error}",
                        jobId, status, error);
                    return IngestResult.FailWithCode(errorCode, error, tableName);
                }
            }

            var delay = remaining < IngestJobPollDelay ? remaining : IngestJobPollDelay;
            if (delay > TimeSpan.Zero)
                await Task.Delay(delay, ct);
        }

        _log.LogWarning("Queued ingest job {JobId} exceeded timeout {TimeoutMs}ms",
            jobId, (int)totalTimeout.TotalMilliseconds);
        return IngestResult.InProgress(
            fallbackTableName,
            errorDetail: $"ingest job {jobId} is still running");
    }
}
