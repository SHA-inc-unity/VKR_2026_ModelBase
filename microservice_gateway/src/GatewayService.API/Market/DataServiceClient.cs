using System.Diagnostics;
using System.Text.Json;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed class DataServiceClient : IDataServiceClient
{
    // Data-service supports a server-side long-poll on cmd.data.dataset.jobs.get
    // via the wait_terminal_ms payload field — see HandleJobsGetAsync.
    // We use a 1500 ms server-side wait + 200 ms client-side back-off, which
    // means a typical chart-ingest run (~300-800 ms with skip_features) finishes
    // in a single Kafka roundtrip instead of the previous ~10-20 roundtrips
    // (50 ms client poll). Falls back to short-poll cleanly if the data-service
    // is older and ignores the field.
    private static readonly TimeSpan IngestJobPollDelay = TimeSpan.FromMilliseconds(200);
    private const int JobsGetServerWaitMs = 1500;

    private readonly IKafkaRequestClient _kafka;
    private readonly MarketSettings      _settings;
    private readonly ILogger<DataServiceClient> _log;

    public DataServiceClient(
        IKafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<DataServiceClient> log)
    {
        _kafka    = kafka;
        _settings = settings.Value;
        _log      = log;
    }

    /// <inheritdoc />
    public async Task<CoverageResult?> GetCoverageAsync(
        string symbol, string bybitInterval, CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetCoverage,
                new { symbol, timeframe = bybitInterval },
                timeout,
                ct);

            return ParseCoverage(reply);
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex, "Coverage Kafka request timed out for {Symbol}/{Interval}",
                symbol, bybitInterval);
            return null;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Coverage Kafka request failed for {Symbol}/{Interval}",
                symbol, bybitInterval);
            return null;
        }
    }

    /// <inheritdoc />
    public async Task<RowsFetchResult> GetLatestWindowRowsAsync(
        string symbol,
        string bybitInterval,
        long stepMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        var tableName = BuildTableName(symbol, bybitInterval);

        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetLatestRows,
                BuildRowsPayload(
                    new Dictionary<string, object?>
                    {
                        ["table"] = tableName,
                        ["step_ms"] = stepMs,
                        ["limit"] = limit,
                    },
                    columns),
                timeout,
                ct);

            if (TryGetReplyError(reply, out var replyError))
            {
                _log.LogWarning(
                    "data-service latest_rows error for {Table} stepMs={StepMs} limit={Limit}: code={Code} detail={Detail}",
                    tableName,
                    stepMs,
                    limit,
                    replyError.Code ?? "n/a",
                    replyError.Detail ?? "unknown error");
                return RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", BuildReplyErrorDetail(replyError));
            }

            if (reply.ValueKind == JsonValueKind.Object &&
                reply.TryGetProperty("claim_check", out _))
            {
                _log.LogWarning(
                    "data-service latest_rows returned a claim-check for {Table} stepMs={StepMs} limit={Limit}",
                    tableName, stepMs, limit);
                return RowsFetchResult.ClaimCheck;
            }

            return RowsFetchResult.From(ParseRows(reply));
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Latest window Kafka request timed out for {Table} stepMs={StepMs} limit={Limit}",
                tableName, stepMs, limit);
            return RowsFetchResult.Fail(
                "DOWNSTREAM_TIMEOUT",
                $"latest_rows timed out for {tableName} stepMs={stepMs} limit={limit}");
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Latest window Kafka request failed for {Table} stepMs={StepMs} limit={Limit}",
                tableName, stepMs, limit);
            return RowsFetchResult.Fail(
                "DATA_SOURCE_UNAVAILABLE",
                $"latest_rows failed for {tableName} stepMs={stepMs} limit={limit}: {ex.Message}");
        }
    }

    /// <inheritdoc />
    public async Task<RowsFetchResult> GetRowsAsync(
        string tableName,
        long startMs,
        long endMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetRows,
                BuildRowsPayload(
                    new Dictionary<string, object?>
                    {
                        ["table"] = tableName,
                        ["start_ms"] = startMs,
                        ["end_ms"] = endMs,
                        ["limit"] = limit,
                    },
                    columns),
                timeout,
                ct);

            if (TryGetReplyError(reply, out var replyError))
            {
                _log.LogWarning(
                    "data-service rows error for {Table} [{Start}..{End}] limit={Limit}: code={Code} detail={Detail}",
                    tableName,
                    startMs,
                    endMs,
                    limit,
                    replyError.Code ?? "n/a",
                    replyError.Detail ?? "unknown error");
                return RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", BuildReplyErrorDetail(replyError));
            }

            if (reply.ValueKind == JsonValueKind.Object &&
                reply.TryGetProperty("claim_check", out _))
            {
                // Payload exceeded Kafka message limit even with the limit parameter.
                // Returning a typed ClaimCheck so ChartService can handle this case
                // without triggering a spurious new ingest (the data IS there).
                _log.LogWarning(
                    "data-service rows returned a claim-check for {Table} [{Start}..{End}] " +
                    "limit={Limit} — payload too large; client should reduce limit",
                    tableName, startMs, endMs, limit);
                return RowsFetchResult.ClaimCheck;
            }

            return RowsFetchResult.From(ParseRows(reply));
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Rows Kafka request timed out for {Table} [{Start}..{End}] limit={Limit}",
                tableName, startMs, endMs, limit);
            return RowsFetchResult.Fail(
                "DOWNSTREAM_TIMEOUT",
                $"rows timed out for {tableName} [{startMs}..{endMs}] limit={limit}");
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Rows Kafka request failed for {Table} [{Start}..{End}] limit={Limit}",
                tableName, startMs, endMs, limit);
            return RowsFetchResult.Fail(
                "DATA_SOURCE_UNAVAILABLE",
                $"rows failed for {tableName} [{startMs}..{endMs}] limit={limit}: {ex.Message}");
        }
    }

    /// <inheritdoc />
    public async Task<IngestResult> IngestAsync(
        string symbol, string bybitInterval, long startMs, long endMs, CancellationToken ct = default)
    {
        var totalTimeout = TimeSpan.FromSeconds(_settings.IngestKafkaTimeoutSeconds);
        var requestTimeout = TimeSpan.FromSeconds(Math.Min(
            _settings.KafkaTimeoutSeconds,
            _settings.IngestKafkaTimeoutSeconds));
        var tableName = BuildTableName(symbol, bybitInterval);

        try
        {
            var startReply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetJobsStart,
                new
                {
                    type = "ingest",
                    target_table = tableName,
                    target_symbol = symbol,
                    target_timeframe = bybitInterval,
                    target_start_ms = startMs,
                    target_end_ms = endMs,
                    created_by = "gateway_market_chart",
                    @params = new
                    {
                        symbol,
                        timeframe = bybitInterval,
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
                    "Ingest job start failed for {Symbol}/{Interval}: code={Code} detail={Detail}",
                    symbol, bybitInterval, errorCode, errorDetail);
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
                    "Ingest job start returned no job_id for {Symbol}/{Interval}",
                    symbol, bybitInterval);
                return IngestResult.FailWithCode(
                    "DATA_SOURCE_UNAVAILABLE",
                    "ingest_job_id_missing",
                    tableName);
            }

            var deduped = startReply.ValueKind == JsonValueKind.Object &&
                startReply.TryGetProperty("deduped", out var dedupedEl) &&
                dedupedEl.ValueKind == JsonValueKind.True;

            _log.LogInformation(
                "Ingest job {JobId} {Mode} for {Symbol}/{Interval} [{StartMs}..{EndMs}]",
                jobId,
                deduped ? "reused" : "started",
                symbol,
                bybitInterval,
                startMs,
                endMs);

            return await WaitForIngestJobAsync(jobId, tableName, totalTimeout, requestTimeout, ct);
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Queued ingest start timed out for {Symbol}/{Interval}",
                symbol, bybitInterval);
            return IngestResult.FailWithCode(
                "DOWNSTREAM_TIMEOUT",
                $"ingest start timed out for {tableName} [{startMs}..{endMs}]",
                tableName);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Ingest Kafka request failed for {Symbol}/{Interval}",
                symbol, bybitInterval);
            return IngestResult.FailWithCode(
                "DATA_SOURCE_UNAVAILABLE",
                $"ingest failed for {tableName} [{startMs}..{endMs}]: {ex.Message}",
                tableName);
        }
    }

    /// <inheritdoc />
    public void FireAndForgetIngest(
        string symbol, string bybitInterval, long startMs, long endMs,
        Action onComplete, Action<Exception> onError)
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
                    CancellationToken.None);

                if (!result.Success)
                {
                    _log.LogError(
                        "Background queued ingest failed for {Symbol}/{Interval}: {Error}",
                        symbol, bybitInterval, result.Error ?? "unknown error");
                    onError(new InvalidOperationException(result.Error ?? "unknown error"));
                }
                else
                {
                    _log.LogInformation(
                        "Background queued ingest completed for {Symbol}/{Interval}: {Rows} rows ingested",
                        symbol, bybitInterval, result.RowsIngested);
                    onComplete();
                }
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Background ingest task failed for {Symbol}/{Interval}",
                    symbol, bybitInterval);
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

    // ── Parsers ───────────────────────────────────────────────────────────

    private static string BuildTableName(string symbol, string bybitInterval) =>
        $"{symbol.ToLowerInvariant()}_{bybitInterval}";

    private static bool TryGetNestedJob(JsonElement el, out JsonElement job)
    {
        if (el.ValueKind == JsonValueKind.Object &&
            el.TryGetProperty("job", out job) &&
            job.ValueKind == JsonValueKind.Object)
        {
            return true;
        }

        job = default;
        return false;
    }

    private static bool TryGetError(JsonElement el, out string error)
    {
        error = string.Empty;
        if (el.ValueKind != JsonValueKind.Object ||
            !el.TryGetProperty("error", out var errEl))
            return false;

        var detail = errEl.ValueKind switch
        {
            JsonValueKind.String => errEl.GetString(),
            _ => errEl.ToString(),
        };
        var code = TryGetString(el, "code");
        error = string.IsNullOrWhiteSpace(code)
            ? detail ?? "unknown error"
            : $"{code}: {detail}";
        return true;
    }

    private static bool TryGetReplyError(JsonElement el, out ReplyError error)
    {
        error = default;
        if (el.ValueKind != JsonValueKind.Object ||
            !el.TryGetProperty("error", out var errEl))
        {
            return false;
        }

        error = new ReplyError(
            TryGetString(el, "code"),
            errEl.ValueKind switch
            {
                JsonValueKind.String => errEl.GetString(),
                _ => errEl.ToString(),
            });
        return true;
    }

    private static string BuildReplyErrorDetail(ReplyError error)
    {
        if (string.IsNullOrWhiteSpace(error.Code))
            return error.Detail ?? "unknown error";

        if (string.IsNullOrWhiteSpace(error.Detail))
            return error.Code!;

        return $"{error.Code}: {error.Detail}";
    }

    private static string NormalizeIngestErrorCode(string? errorCode, string fallbackCode)
    {
        if (string.IsNullOrWhiteSpace(errorCode))
            return fallbackCode;

        return errorCode.Trim().ToUpperInvariant() switch
        {
            "DOWNSTREAM_TIMEOUT" => "DOWNSTREAM_TIMEOUT",
            "DATA_SOURCE_UNAVAILABLE" => "DATA_SOURCE_UNAVAILABLE",
            "SERVICE_BUSY" => "SERVICE_BUSY",
            _ => fallbackCode,
        };
    }

    private static string BuildJobFailure(JsonElement job, string fallbackStatus)
    {
        var errorCode = TryGetString(job, "error_code");
        var errorMessage = TryGetString(job, "error_message");
        if (!string.IsNullOrWhiteSpace(errorMessage))
            return errorMessage!;
        if (!string.IsNullOrWhiteSpace(errorCode))
            return errorCode!;
        return $"job_{fallbackStatus}";
    }

    private static string? TryGetString(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var value)) return null;
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Number => value.ToString(),
            JsonValueKind.True => bool.TrueString,
            JsonValueKind.False => bool.FalseString,
            _ => null,
        };
    }

    private static int ClampToInt(long value)
    {
        if (value <= 0) return 0;
        if (value >= int.MaxValue) return int.MaxValue;
        return (int)value;
    }

    private static CoverageResult? ParseCoverage(JsonElement el)
    {
        if (el.ValueKind != JsonValueKind.Object)
            return null;

        if (el.TryGetProperty("error", out _))
            return null;

        var exists = el.TryGetProperty("exists", out var existsEl) &&
                     existsEl.ValueKind == JsonValueKind.True;

        var tableName = el.TryGetProperty("table_name", out var tnEl)
            ? tnEl.GetString() ?? string.Empty
            : string.Empty;

        if (!exists)
            return new CoverageResult(false, tableName, 0, 0, 0, 0.0);

        var rows    = GetLong(el, "rows");
        var minTs   = GetLong(el, "min_ts_ms");
        var maxTs   = GetLong(el, "max_ts_ms");
        var covPct  = el.TryGetProperty("coverage_pct", out var cpEl) &&
                      cpEl.ValueKind == JsonValueKind.Number
                      ? cpEl.GetDouble()
                      : 0.0;

        return new CoverageResult(true, tableName, rows, minTs, maxTs, covPct);
    }

    private IReadOnlyList<CandleRow> ParseRows(JsonElement el)
    {
        if (el.ValueKind != JsonValueKind.Object ||
            !el.TryGetProperty("rows", out var rowsEl) ||
            rowsEl.ValueKind != JsonValueKind.Array)
            return [];

        var result = new List<CandleRow>();
        foreach (var row in rowsEl.EnumerateArray())
        {
            if (row.ValueKind != JsonValueKind.Object)
                continue;

            var tsMs     = GetLong(row, "timestamp_ms");
            var open     = GetDecimal(row, "open_price");
            var high     = GetDecimal(row, "high_price");
            var low      = GetDecimal(row, "low_price");
            var close    = GetDecimal(row, "close_price");
            var volume   = GetDecimal(row, "volume");
            var turnover = GetDecimal(row, "turnover");

            // Skip rows with invalid OHLC values
            if (tsMs == 0 || open == 0 || close == 0)
                continue;

            result.Add(new CandleRow(tsMs, open, high, low, close, volume, turnover));
        }

        return result;
    }

    private static long GetLong(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var v)) return 0;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n)) return n;
        if (v.ValueKind == JsonValueKind.String &&
            long.TryParse(v.GetString(), out var s)) return s;
        return 0;
    }

    private static decimal GetDecimal(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var v)) return 0;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetDecimal(out var d)) return d;
        if (v.ValueKind == JsonValueKind.String &&
            decimal.TryParse(v.GetString(),
                System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture,
                out var s))
            return s;
        return 0;
    }

    private readonly record struct ReplyError(string? Code, string? Detail);

    /// <summary>
    /// Columns the chart path actually consumes from data-service rows replies.
    /// Used to ask the data-service to project only these columns instead of
    /// returning the full feature-engineered row (40+ columns), which was the
    /// dominant cost behind the Kafka rows timeout on cold tables.
    /// </summary>
    public static readonly IReadOnlyList<string> ChartProjectionColumns = new[]
    {
        "timestamp_utc",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "turnover",
    };

    private static Dictionary<string, object?> BuildRowsPayload(
        Dictionary<string, object?> basePayload,
        IReadOnlyList<string>? columns)
    {
        if (columns is { Count: > 0 })
            basePayload["columns"] = columns;
        return basePayload;
    }
}
