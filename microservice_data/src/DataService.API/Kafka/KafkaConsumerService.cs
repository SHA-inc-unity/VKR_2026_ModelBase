using System.IO.Compression;
using System.IO.Pipelines;
using System.Text.Json;
using Confluent.Kafka;
using DataService.API.Bybit;
using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Jobs;
using DataService.API.Markets;
using DataService.API.Minio;
using DataService.API.Settings;
using Microsoft.Extensions.Options;
using Npgsql;

namespace DataService.API.Kafka;

/// <summary>
/// BackgroundService that consumes all cmd.data.* Kafka topics and dispatches handlers.
/// </summary>
public sealed partial class KafkaConsumerService : BackgroundService
{
    private readonly IConsumer<string, string> _consumer;
    private readonly KafkaProducer              _producer;
    private readonly DatasetRepository          _repo;
    private readonly DatasetJobsRepository      _jobsRepo;
    private readonly CurrencyPairsRepository    _pairsRepo;
    private readonly MarketWatchRepository      _marketWatchRepo;
    private readonly MarketWatcherRuntimeState  _marketWatcher;
    private readonly MarketDataClientFactory    _markets;
    private readonly MinioClaimCheckService     _minio;
    private readonly Jobs.JobDispatchChannel    _jobDispatch;
    private readonly Jobs.JobCompletionTracker  _jobCompletion;
    // Browser-facing origin для presigned URL'ов, которые получает admin
    // и в итоге показывает в браузере (CSV/ZIP экспорт, anomaly report).
    // По умолчанию — внешний вход infra-nginx на host-порте 8501; путь
    // /modelline-blobs/* проксируется в minio:9000 без потери query.
    private readonly string                     _browserDownloadBaseUrl;
    // Internal S3 endpoint (http://minio:9000). Используется для
    // server-to-server presigned URL'ов (export_full → analitic),
    // которые потребляются изнутри docker-сети, где host браузерного
    // входа не резолвится.
    private readonly string                     _internalDownloadBaseUrl;
    private readonly ILogger<KafkaConsumerService> _log;

    // Two-tier concurrency control. The whole consume loop is bounded by
    // _concurrency; *heavy* SQL/export/anomaly handlers also acquire
    // _heavyConcurrency so a burst of e.g. anomaly-detect requests cannot
    // starve light health/coverage/list traffic that shares the same
    // PostgreSQL pool.
    //
    // Tuning rationale (Npgsql default pool size = 100):
    //   - heavy slots × ~5-10 fan-out connections per heavy run ≈ 40-80
    //   - light handlers re-use the remaining pool for coverage/list/etc.
    //
    // Previously heavy was 4 — a single in-flight admin export plus two
    // detect-anomalies runs would saturate the slot and force chart-related
    // legacy ingests to queue behind them. Raising to 8 keeps the pool budget
    // safe (8 × 10 = 80 < 100) while letting admin exports run concurrently
    // with chart-path legacy ingests. The job-based chart-ingest path
    // (cmd.data.dataset.jobs.start) is NOT in HeavyTopics and is unaffected.
    private readonly SemaphoreSlim _concurrency      = new(32, 32);
    private readonly SemaphoreSlim _heavyConcurrency = new(8,  8);

    // Topics that fan out into multiple parallel SQL queries or stream large
    // payloads. They share the heavy-ops semaphore.
    private static readonly HashSet<string> HeavyTopics = new(StringComparer.Ordinal)
    {
        Topics.CmdDataDatasetExport,
        Topics.CmdDataDatasetIngest,
        Topics.CmdDataDatasetDetectAnomalies,
        Topics.CmdDataDatasetCleanPreview,
        Topics.CmdDataDatasetCleanApply,
        Topics.CmdDataDatasetComputeFeatures,
        Topics.CmdDataDatasetImportCsv,
        Topics.CmdDataDatasetUpsertOhlcv,
        Topics.CmdDataDatasetRepairOhlcv,
        Topics.CmdDataDatasetColumnStats,
        Topics.CmdDataDatasetColumnHistogram,
    };

    // Payloads larger than this go through MinIO claim-check
    private const int InlinePayloadLimit = 512 * 1024; // 512 KB

    // Anomaly response: how many sample rows to inline. Above this we still
    // return the full grouped summary, but the row sample is capped and the
    // complete report is published to MinIO so the UI can fetch it via a
    // presigned URL on demand.
    private const int AnomalyInlineRowSample = 200;
    private const string EvtAnaliticDatasetRepairProgress = "events.analitic.dataset.repair.progress";

    public KafkaConsumerService(
        IOptions<DataServiceSettings> opts,
        KafkaProducer producer,
        DatasetRepository repo,
        DatasetJobsRepository jobsRepo,
        CurrencyPairsRepository pairsRepo,
        MarketWatchRepository marketWatchRepo,
        MarketWatcherRuntimeState marketWatcher,
        MarketDataClientFactory markets,
        MinioClaimCheckService minio,
        Jobs.JobDispatchChannel jobDispatch,
        Jobs.JobCompletionTracker jobCompletion,
        ILogger<KafkaConsumerService> log)
    {
        _producer                = producer;
        _repo                    = repo;
        _jobsRepo                = jobsRepo;
        _pairsRepo               = pairsRepo;
        _marketWatchRepo         = marketWatchRepo;
        _marketWatcher           = marketWatcher;
        _markets                 = markets;
        _minio                   = minio;
        _jobDispatch             = jobDispatch;
        _jobCompletion           = jobCompletion;
        _browserDownloadBaseUrl  = opts.Value.Minio.PublicDownloadBaseUrl;
        _internalDownloadBaseUrl = opts.Value.Minio.Endpoint;
        _log                     = log;

        var cfg = new ConsumerConfig
        {
            BootstrapServers         = opts.Value.Kafka.BootstrapServers,
            GroupId                  = "microservice_data",
            AutoOffsetReset          = AutoOffsetReset.Earliest,
            EnableAutoCommit         = true,
            AllowAutoCreateTopics    = true,
            // Keep metadata refresh reasonable so subscription picks up newly
            // created topics without waiting too long.
            TopicMetadataRefreshIntervalMs = 5000,
        };
        _consumer = new ConsumerBuilder<string, string>(cfg)
            .SetErrorHandler((_, err) =>
            {
                // librdkafka reports "Subscribed topic not available" as non-fatal
                // errors when Redpanda has not yet created the topic. Don't crash
                // — just log at a low level; the consumer will retry internally.
                if (err.IsFatal)
                {
                    _log.LogError("Kafka fatal error: {Code} {Reason}", err.Code, err.Reason);
                }
                else
                {
                    _log.LogDebug("Kafka non-fatal: {Code} {Reason}", err.Code, err.Reason);
                }
            })
            .Build();
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("KafkaConsumerService started, topics: {Topics}",
            string.Join(", ", Topics.AllConsumed));

        // BackgroundService.StartAsync invokes ExecuteAsync synchronously until
        // the first incomplete await. Without an early yield a successful
        // subscribe drops straight into the blocking Consume() loop and holds
        // up host startup, which delays HTTP health/readiness and other hosted
        // services such as DatasetJobRunner.
        await Task.Yield();

        // ── Resilient subscribe: retry until Redpanda is reachable ──
        await SubscribeWithRetryAsync(stoppingToken);

        while (!stoppingToken.IsCancellationRequested)
        {
            ConsumeResult<string, string>? result = null;
            try
            {
                result = _consumer.Consume(TimeSpan.FromMilliseconds(200));
            }
            catch (OperationCanceledException) { break; }
            catch (ConsumeException cex) when (IsTransientConsumeError(cex.Error))
            {
                // Topic not yet available, leader election, rebalance — transient.
                // Keep consuming: librdkafka will retry automatically once metadata refreshes.
                _log.LogDebug("Transient Kafka consume error: {Code} {Reason}",
                    cex.Error.Code, cex.Error.Reason);
                await Task.Delay(1000, stoppingToken);
                continue;
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Kafka consume error");
                await Task.Delay(1000, stoppingToken);
                continue;
            }

            if (result is null) continue;

            // Fire-and-forget with two-tier concurrency limit.
            // The outer semaphore caps total in-flight handlers; heavy
            // topics also acquire a dedicated slot so their fan-out cannot
            // exhaust the PostgreSQL pool and starve light handlers.
            _ = Task.Run(async () =>
            {
                await _concurrency.WaitAsync(stoppingToken);
                bool acquiredHeavy = false;
                JsonDocument? doc = null;
                try
                {
                    if (HeavyTopics.Contains(result.Topic))
                    {
                        await _heavyConcurrency.WaitAsync(stoppingToken);
                        acquiredHeavy = true;
                    }

                    doc = JsonDocument.Parse(result.Message.Value);
                    var root         = doc.RootElement;
                    var correlationId = root.TryGetProperty("correlation_id", out var cid) ? cid.GetString() ?? "" : "";
                    var replyTo      = root.TryGetProperty("reply_to", out var rt) ? rt.GetString() ?? "" : "";
                    var payload      = root.TryGetProperty("payload", out var p) ? p : default;

                    await DispatchAsync(result.Topic, correlationId, replyTo, payload, stoppingToken);
                }
                catch (Exception ex)
                {
                    _log.LogError(ex, "Handler error on topic {Topic}", result.Topic);
                }
                finally
                {
                    doc?.Dispose();
                    if (acquiredHeavy) _heavyConcurrency.Release();
                    _concurrency.Release();
                }
            }, stoppingToken);
        }

        _consumer.Close();
    }

    // ── Resilience helpers ──────────────────────────────────────────────────

    private async Task SubscribeWithRetryAsync(CancellationToken ct)
    {
        var attempt = 0;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                _consumer.Subscribe(Topics.AllConsumed);
                _log.LogInformation("Subscribed to {Count} Kafka topics", Topics.AllConsumed.Length);
                return;
            }
            catch (Exception ex)
            {
                attempt++;
                var delay = TimeSpan.FromSeconds(Math.Min(30, Math.Pow(2, attempt)));
                _log.LogWarning(ex, "Subscribe failed (attempt {Attempt}); retrying in {Delay}s",
                    attempt, delay.TotalSeconds);
                try { await Task.Delay(delay, ct); } catch (OperationCanceledException) { return; }
            }
        }
    }

    private static bool IsTransientConsumeError(Error err) =>
        !err.IsFatal && (
            err.Code == ErrorCode.UnknownTopicOrPart ||
            err.Code == ErrorCode.Local_UnknownTopic ||
            err.Code == ErrorCode.Local_UnknownPartition ||
            err.Code == ErrorCode.LeaderNotAvailable ||
            err.Code == ErrorCode.NotCoordinatorForGroup ||
            err.Code == ErrorCode.GroupLoadInProgress);

    // ── Safe JSON accessors ────────────────────────────────────────────────

    private static string? TryGetString(JsonElement p, string name)
    {
        if (p.ValueKind != JsonValueKind.Object) return null;
        return p.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;
    }

    private static long? TryGetInt64(JsonElement p, string name)
    {
        if (p.ValueKind != JsonValueKind.Object) return null;
        if (!p.TryGetProperty(name, out var v)) return null;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n)) return n;
        if (v.ValueKind == JsonValueKind.String && long.TryParse(v.GetString(), out var s)) return s;
        return null;
    }

    private static decimal? TryGetDecimal(JsonElement p, string name)
    {
        if (p.ValueKind != JsonValueKind.Object) return null;
        if (!p.TryGetProperty(name, out var v)) return null;
        if (v.ValueKind == JsonValueKind.Null) return null;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetDecimal(out var d)) return d;
        if (v.ValueKind == JsonValueKind.String
            && decimal.TryParse(v.GetString(),
                System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture,
                out var s)) return s;
        return null;
    }

    private static bool? TryGetBool(JsonElement p, string name)
    {
        if (p.ValueKind != JsonValueKind.Object || !p.TryGetProperty(name, out var v)) return null;
        return v.ValueKind switch
        {
            JsonValueKind.True  => true,
            JsonValueKind.False => false,
            JsonValueKind.String when bool.TryParse(v.GetString(), out var b) => b,
            _ => null,
        };
    }

    private static IReadOnlyList<string>? TryGetStringArray(JsonElement p, string name)
    {
        if (p.ValueKind != JsonValueKind.Object || !p.TryGetProperty(name, out var v))
            return null;
        if (v.ValueKind != JsonValueKind.Array)
            return null;
        var list = new List<string>(v.GetArrayLength());
        foreach (var el in v.EnumerateArray())
        {
            if (el.ValueKind == JsonValueKind.String)
            {
                var s = el.GetString();
                if (!string.IsNullOrWhiteSpace(s))
                    list.Add(s!);
            }
        }
        return list.Count == 0 ? null : list;
    }

    private async Task DispatchAsync(
        string topic, string correlationId, string replyTo,
        JsonElement payload, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(replyTo))
        {
            _log.LogWarning("Message on {Topic} has no reply_to, skipping", topic);
            return;
        }

        object response = topic switch
        {
            Topics.CmdDataHealth             => await HandleHealthAsync(ct),
            Topics.CmdDataDbPing             => await HandleDbPingAsync(ct),
            Topics.CmdDataDatasetListTables  => await HandleListTablesAsync(ct),
            Topics.CmdDataDatasetCoverage    => await HandleCoverageAsync(payload, ct),
            Topics.CmdDataDatasetTimestamps  => await HandleTimestampsAsync(payload, ct),
            Topics.CmdDataDatasetMissing     => await HandleFindMissingAsync(payload, ct),
            Topics.CmdDataDatasetRows        => await HandleRowsAsync(payload, replyTo, correlationId, ct),
            Topics.CmdDataDatasetLatestRows  => await HandleLatestRowsAsync(payload, replyTo, correlationId, ct),
            Topics.CmdDataDatasetExport      => await HandleExportAsync(payload, replyTo, correlationId, ct),
            Topics.CmdDataDatasetExportFull  => await HandleExportFullAsync(payload, ct),
            Topics.CmdDataDatasetSchema      => await HandleTableSchemaAsync(payload, ct),
            Topics.CmdDataDatasetNormalizeTf => HandleNormalizeTimeframe(payload),
            Topics.CmdDataDatasetMakeTable   => HandleMakeTableName(payload),
            Topics.CmdDataDatasetInstrument  => await HandleInstrumentDetailsAsync(payload, ct),
            Topics.CmdDataDatasetConstants   => HandleConstants(),
            Topics.CmdDataDatasetIngest      => await HandleIngestAsync(payload, correlationId, ct),
            Topics.CmdDataDatasetDeleteRows  => await HandleDeleteRowsAsync(payload, ct),
            Topics.CmdDataDatasetImportCsv   => await HandleImportCsvAsync(payload, ct),
            Topics.CmdDataDatasetColumnStats     => await HandleColumnStatsAsync(payload, ct),
            Topics.CmdDataDatasetColumnHistogram => await HandleColumnHistogramAsync(payload, ct),
            Topics.CmdDataDatasetBrowse          => await HandleBrowseAsync(payload, ct),
            Topics.CmdDataDatasetSeries          => await HandleSeriesAsync(payload, ct),
            Topics.CmdDataDatasetComputeFeatures => await HandleComputeFeaturesAsync(payload, ct),
            Topics.CmdDataDatasetDetectAnomalies => await HandleDetectAnomaliesAsync(payload, ct),
            Topics.CmdDataDatasetCleanPreview    => await HandleCleanPreviewAsync(payload, ct),
            Topics.CmdDataDatasetCleanApply      => await HandleCleanApplyAsync(payload, ct),
            Topics.CmdDataDatasetAuditLog        => await HandleAuditLogAsync(payload, ct),
            Topics.CmdDataDatasetUpsertOhlcv     => await HandleUpsertOhlcvAsync(payload, ct),
            Topics.CmdDataDatasetRepairOhlcv     => await HandleRepairOhlcvAsync(payload, correlationId, ct),
            Topics.CmdDataDatasetJobsStart       => await HandleJobsStartAsync(payload, ct),
            Topics.CmdDataDatasetJobsCancel      => await HandleJobsCancelAsync(payload, ct),
            Topics.CmdDataDatasetJobsGet         => await HandleJobsGetAsync(payload, ct),
            Topics.CmdDataDatasetJobsList        => await HandleJobsListAsync(payload, ct),
            Topics.CmdDataMarketWatcherStatus    => HandleMarketWatcherStatus(),
            Topics.CmdDataMarketWatcherSetEnabled=> HandleMarketWatcherSetEnabled(payload),
            Topics.CmdDataMarketWatcherRows      => await HandleMarketWatcherRowsAsync(payload, ct),
            Topics.CmdDataMarketWatcherLogs      => HandleMarketWatcherLogs(payload),
            Topics.CmdDataMarketWatcherTracked   => await HandleMarketWatcherTrackedSymbolsAsync(payload, ct),
            Topics.CmdDataPairsList      => await HandlePairsListAsync(ct),
            Topics.CmdDataPairsAdd       => await HandlePairsAddAsync(payload, ct),
            Topics.CmdDataPairsRemove    => await HandlePairsRemoveAsync(payload, ct),
            Topics.CmdDataPairsSetActive => await HandlePairsSetActiveAsync(payload, ct),
            _                                => new { error = $"Unknown topic: {topic}" },
        };

        await _producer.PublishReplyAsync(replyTo, correlationId, response, ct);
    }

    // ── Handlers ──────────────────────────────────────────────────────────

    private static Task<object> HandleHealthAsync(CancellationToken _) =>
        Task.FromResult<object>(new { status = "ok", service = "microservice_data" });

    private async Task<object> HandleDbPingAsync(CancellationToken ct)
    {
        var ok = await _repo.PingAsync(ct);
        return new { status = ok ? "ok" : "error" };
    }

    private async Task<object> HandleListTablesAsync(CancellationToken ct)
    {
        var names = await _repo.ListTablesAsync(ct);

        // Cheap path: fetch only table bounds for every table in parallel.
        // Exact COUNT(*) coverage is reserved for explicit on-demand coverage checks.
        var tasks = names.Select(async name =>
        {
            try
            {
                var bounds = await _repo.GetBoundsIfExistsAsync(name, ct);
                return BuildTableBoundsInfo(name, bounds);
            }
            catch
            {
                return BuildTableBoundsInfo(name, null);
            }
        });
        var tables = await Task.WhenAll(tasks);
        return new { tables };
    }

    /// <summary>
    /// Builds the public shape used by the admin dashboard's "Available Tables"
    /// list. Computes <c>coverage_pct</c> from rows / expected candles derived
    /// from the table-name timeframe suffix, and exposes dates as ISO
    /// <c>YYYY-MM-DD</c>. Returns zero/null fields for empty tables.
    /// </summary>
    private static object BuildTableInfo(
        string tableName,
        (long Rows, long MinTsMs, long MaxTsMs)? cov)
    {
        if (cov is null || cov.Value.Rows == 0)
        {
            return new
            {
                table_name   = tableName,
                rows         = 0L,
                coverage_pct = 0.0,
                date_from    = (string?)null,
                date_to      = (string?)null,
            };
        }

        var stepMs = TryGetStepMsFromTableName(tableName);
        double pct = 0.0;
        if (stepMs is not null && cov.Value.MaxTsMs > cov.Value.MinTsMs)
        {
            var expected = Math.Max(1L, (cov.Value.MaxTsMs - cov.Value.MinTsMs) / stepMs.Value + 1);
            pct = Math.Min(100.0, Math.Round((double)cov.Value.Rows / expected * 100.0, 2));
        }

        return new
        {
            table_name   = tableName,
            rows         = cov.Value.Rows,
            coverage_pct = pct,
            date_from    = FormatDate(cov.Value.MinTsMs),
            date_to      = FormatDate(cov.Value.MaxTsMs),
        };
    }

    private static object BuildTableBoundsInfo(
        string tableName,
        (long MinTsMs, long MaxTsMs)? bounds)
    {
        if (bounds is null)
        {
            return new
            {
                table_name   = tableName,
                rows         = 0L,
                rows_known   = false,
                coverage_pct = (double?)null,
                date_from    = (string?)null,
                date_to      = (string?)null,
            };
        }

        long approxRows = 0L;
        var stepMs = TryGetStepMsFromTableName(tableName);
        if (stepMs is long step && bounds.Value.MaxTsMs >= bounds.Value.MinTsMs)
        {
            approxRows = Math.Max(1L, (bounds.Value.MaxTsMs - bounds.Value.MinTsMs) / step + 1);
        }

        return new
        {
            table_name   = tableName,
            rows         = approxRows,
            rows_known   = false,
            coverage_pct = (double?)null,
            date_from    = FormatDate(bounds.Value.MinTsMs),
            date_to      = FormatDate(bounds.Value.MaxTsMs),
        };
    }

    private static long? TryGetStepMsFromTableName(string tableName)
    {
        var idx = tableName.LastIndexOf('_');
        if (idx < 0 || idx == tableName.Length - 1) return null;
        var tf = tableName[(idx + 1)..];
        try
        {
            var (_, _, stepMs) = DatasetCore.NormalizeTimeframe(tf);
            return stepMs;
        }
        catch { return null; }
    }

    private static string FormatDate(long ms) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ms).UtcDateTime.ToString("yyyy-MM-dd");

    public override void Dispose()
    {
        _consumer.Dispose();
        base.Dispose();
    }

}
