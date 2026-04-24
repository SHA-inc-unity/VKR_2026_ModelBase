using System.IO.Compression;
using System.IO.Pipelines;
using System.Text.Json;
using Confluent.Kafka;
using DataService.API.Bybit;
using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Minio;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Kafka;

/// <summary>
/// BackgroundService that consumes all cmd.data.* Kafka topics and dispatches handlers.
/// </summary>
public sealed class KafkaConsumerService : BackgroundService
{
    private readonly IConsumer<string, string> _consumer;
    private readonly KafkaProducer              _producer;
    private readonly DatasetRepository          _repo;
    private readonly BybitApiClient             _bybit;
    private readonly MinioClaimCheckService     _minio;
    private readonly string                     _minioPublicUrl;
    private readonly ILogger<KafkaConsumerService> _log;

    // Limit concurrent in-flight message handlers (not static — instance field)
    private readonly SemaphoreSlim _concurrency = new(32, 32);

    // Payloads larger than this go through MinIO claim-check
    private const int InlinePayloadLimit = 512 * 1024; // 512 KB

    public KafkaConsumerService(
        IOptions<DataServiceSettings> opts,
        KafkaProducer producer,
        DatasetRepository repo,
        BybitApiClient bybit,
        MinioClaimCheckService minio,
        ILogger<KafkaConsumerService> log)
    {
        _producer       = producer;
        _repo           = repo;
        _bybit          = bybit;
        _minio          = minio;
        _minioPublicUrl = opts.Value.Minio.PublicUrl;
        _log            = log;

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

            // Fire-and-forget with concurrency limit
            _ = Task.Run(async () =>
            {
                await _concurrency.WaitAsync(stoppingToken);
                JsonDocument? doc = null;
                try
                {
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
            Topics.CmdDataDatasetExport      => await HandleExportAsync(payload, replyTo, correlationId, ct),
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
            Topics.CmdDataDatasetComputeFeatures => await HandleComputeFeaturesAsync(payload, ct),
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

        // Fetch coverage for every table in parallel; each item always has
        // the shape front-end expects (table_name, rows, coverage_pct, date_from, date_to),
        // even when the table is empty or coverage is unavailable.
        var tasks = names.Select(async name =>
        {
            try
            {
                var cov = await _repo.GetCoverageIfExistsAsync(name, ct);
                return BuildTableInfo(name, cov);
            }
            catch
            {
                return BuildTableInfo(name, null);
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

    private async Task<object> HandleCoverageAsync(JsonElement p, CancellationToken ct)
    {
        // Resolve table name from either explicit { table } or { symbol, timeframe }.
        var table = TryGetString(p, "table");
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        long? stepMs = null;

        if (string.IsNullOrEmpty(table))
        {
            if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe))
                return new { error = "missing fields: table, or (symbol + timeframe)" };
            try
            {
                var (key, _, tfStep) = DatasetCore.NormalizeTimeframe(timeframe);
                stepMs = tfStep;
                table  = DatasetCore.MakeTableName(symbol, key);
            }
            catch (ArgumentException ex) { return new { error = ex.Message }; }
        }
        else if (!string.IsNullOrEmpty(timeframe))
        {
            // table supplied but we can still learn the step from an explicit timeframe
            try { stepMs = DatasetCore.NormalizeTimeframe(timeframe).StepMs; }
            catch { /* ignore — fall back to table-name derivation */ }
        }

        stepMs ??= TryGetStepMsFromTableName(table);

        var startMs = TryGetInt64(p, "start_ms");
        var endMs   = TryGetInt64(p, "end_ms");

        var cov = await _repo.GetCoverageIfExistsAsync(table, ct);
        if (cov is null)
        {
            return new
            {
                exists       = false,
                table_name   = table,
                rows         = 0L,
                expected     = 0L,
                coverage_pct = 0.0,
                gaps         = 0L,
                min_ts_ms    = (long?)null,
                max_ts_ms    = (long?)null,
                date_from    = (string?)null,
                date_to      = (string?)null,
            };
        }

        // Expected candle count: prefer the explicit [start_ms, end_ms] window
        // (matches what front-end asked about), otherwise fall back to the
        // observed data range.
        long expected = 0;
        if (stepMs is long step && step > 0)
        {
            if (startMs is not null && endMs is not null && endMs.Value > startMs.Value)
                expected = Math.Max(0, (endMs.Value - startMs.Value) / step + 1);
            else if (cov.Value.MaxTsMs > cov.Value.MinTsMs)
                expected = Math.Max(0, (cov.Value.MaxTsMs - cov.Value.MinTsMs) / step + 1);
        }

        double pct = expected > 0
            ? Math.Min(100.0, Math.Round((double)cov.Value.Rows / expected * 100.0, 2))
            : 0.0;
        long gaps = expected > cov.Value.Rows ? expected - cov.Value.Rows : 0L;

        return new
        {
            exists       = true,
            table_name   = table,
            rows         = cov.Value.Rows,
            min_ts_ms    = cov.Value.MinTsMs,
            max_ts_ms    = cov.Value.MaxTsMs,
            expected,
            coverage_pct = pct,
            gaps,
            date_from    = FormatDate(cov.Value.MinTsMs),
            date_to      = FormatDate(cov.Value.MaxTsMs),
        };
    }

    private async Task<object> HandleTimestampsAsync(JsonElement p, CancellationToken ct)
    {
        var table   = TryGetString(p, "table");
        var startMs = TryGetInt64(p, "start_ms");
        var endMs   = TryGetInt64(p, "end_ms");
        if (string.IsNullOrEmpty(table) || startMs is null || endMs is null)
            return new { error = "missing fields: table, start_ms, end_ms" };

        var ts = await _repo.FetchTimestampsAsync(table, startMs.Value, endMs.Value, ct);
        return new { timestamps = ts };
    }

    private async Task<object> HandleFindMissingAsync(JsonElement p, CancellationToken ct)
    {
        var table   = TryGetString(p, "table");
        var startMs = TryGetInt64(p, "start_ms");
        var endMs   = TryGetInt64(p, "end_ms");
        var stepMs  = TryGetInt64(p, "step_ms");
        if (string.IsNullOrEmpty(table) || startMs is null || endMs is null || stepMs is null)
            return new { error = "missing fields: table, start_ms, end_ms, step_ms" };

        var missing = await _repo.FindMissingTimestampsAsync(table, startMs.Value, endMs.Value, stepMs.Value, ct);
        return new { missing };
    }

    private async Task<object> HandleRowsAsync(
        JsonElement p, string replyTo, string correlationId, CancellationToken ct)
    {
        var table   = TryGetString(p, "table");
        var startMs = TryGetInt64(p, "start_ms");
        var endMs   = TryGetInt64(p, "end_ms");
        if (string.IsNullOrEmpty(table) || startMs is null || endMs is null)
            return new { error = "missing fields: table, start_ms, end_ms" };

        var rows = await _repo.FetchRowsAsync(table, startMs.Value, endMs.Value, ct);
        var json = JsonSerializer.SerializeToUtf8Bytes(new { rows });
        if (json.Length > InlinePayloadLimit)
        {
            var claim = await _minio.PutBytesAsync(json, contentType: "application/json", ct: ct);
            return new { claim_check = claim };
        }
        return new { rows };
    }

    private async Task<object> HandleExportAsync(
        JsonElement p, string replyTo, string correlationId, CancellationToken ct)
    {
        var startMs = TryGetInt64(p, "start_ms");
        var endMs   = TryGetInt64(p, "end_ms");
        if (startMs is null || endMs is null)
            return new { error = "missing fields: start_ms, end_ms" };

        // ── ZIP mode: payload.tables is a string array ────────────────────
        // When Admin asks to export *all* timeframes for a symbol, we don't
        // want to force the browser to juggle 11 parallel downloads (Chromium
        // suppresses all but the first few programmatic clicks). Instead we
        // bundle every per-timeframe CSV into a single ZIP, park it in
        // MinIO, and hand back a claim-check — Admin fetches and streams
        // the one archive back to the browser.
        //
        // The ZIP itself is built in memory (ZipArchiveMode.Create needs a
        // writable stream; seekable MemoryStream is the simplest match).
        // For the individual CSVs we stream directly from PostgreSQL (COPY
        // TO STDOUT) into each ZIP entry — so the only thing we buffer is
        // the compressed ZIP output, not the raw CSV text.
        if (p.ValueKind == JsonValueKind.Object
            && p.TryGetProperty("tables", out var tablesEl)
            && tablesEl.ValueKind == JsonValueKind.Array)
        {
            var tables = tablesEl.EnumerateArray()
                .Where(e => e.ValueKind == JsonValueKind.String)
                .Select(e => e.GetString() ?? "")
                .Where(s => !string.IsNullOrWhiteSpace(s))
                .ToList();
            if (tables.Count == 0)
                return new { error = "tables must be a non-empty array of strings" };

            try
            {
                using var ms = new MemoryStream();
                using (var archive = new ZipArchive(ms, ZipArchiveMode.Create, leaveOpen: true))
                {
                    foreach (var tableName in tables)
                    {
                        // Fastest — keep CSV text readable; ZIP size is dominated
                        // by compression ratio here, so Optimal pays off.
                        var entry = archive.CreateEntry($"{tableName}.csv", CompressionLevel.Optimal);
                        await using var entryStream = entry.Open();
                        await _repo.ExportCsvToStreamAsync(
                            tableName, startMs.Value, endMs.Value, entryStream, ct);
                    }
                } // archive.Dispose() writes the central directory

                var zipBytes = ms.ToArray();
                var zipKey   = $"exports/{Guid.NewGuid():N}.zip";
                var claim = await _minio.PutBytesAsync(
                    zipBytes, zipKey, contentType: "application/zip", ct: ct);

                _log.LogInformation(
                    "[export:zip] tables={Count} bytes={Bytes} key={Key}",
                    tables.Count, zipBytes.Length, zipKey);

                return new { claim_check = claim };
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "zip export failed for tables={Tables}", string.Join(",", tables));
                return new { error = ex.Message };
            }
        }

        // ── Single-table mode: streaming CSV → presigned URL ──────────────
        var table = TryGetString(p, "table");
        if (string.IsNullOrEmpty(table))
            return new { error = "missing fields: table (or tables), start_ms, end_ms" };

        // Streaming export pipeline:
        //   PostgreSQL COPY TO STDOUT → TextReader → pipe → TransferUtility → MinIO
        //
        // Two tasks run concurrently, connected by a System.IO.Pipelines pipe:
        //   • producer: ExportCsvToStreamAsync writes CSV bytes into pipe.Writer
        //   • consumer: PutStreamAsync reads pipe.Reader and multipart-uploads to MinIO
        //
        // Neither side buffers the full payload in memory — peak RAM is the
        // pipe's internal buffer (~default 64 KB) plus one TransferUtility
        // part (5 MB). The 10 GB-for-1m-over-5-years blow-up is gone.
        //
        // After both tasks complete we hand out a presigned URL so the
        // browser fetches the object straight from MinIO — bytes never flow
        // back through this service again.
        var key = $"exports/{Guid.NewGuid():N}.csv";
        // leaveOpen: true on both AsStream() calls is deliberate — we complete
        // the underlying Pipe sides explicitly (with or without an exception)
        // so that error propagation survives Stream.DisposeAsync(), which
        // would otherwise silently turn a failure into a clean EOF and let
        // the consumer think the export finished successfully.
        var pipe = new Pipe();

        Exception? producerError = null;
        var producerTask = Task.Run(async () =>
        {
            Exception? captured = null;
            try
            {
                await using var writer = pipe.Writer.AsStream(leaveOpen: true);
                await _repo.ExportCsvToStreamAsync(
                    table, startMs.Value, endMs.Value, writer, ct);
            }
            catch (Exception ex) { captured = ex; producerError = ex; }
            await pipe.Writer.CompleteAsync(captured);
        }, ct);

        Exception? consumerError = null;
        var consumerTask = Task.Run(async () =>
        {
            Exception? captured = null;
            try
            {
                await using var reader = pipe.Reader.AsStream(leaveOpen: true);
                await _minio.PutStreamAsync(reader, key, "text/csv; charset=utf-8", ct);
            }
            catch (Exception ex) { captured = ex; consumerError = ex; }
            await pipe.Reader.CompleteAsync(captured);
        }, ct);

        await Task.WhenAll(producerTask, consumerTask);

        var err = producerError ?? consumerError;
        if (err is not null)
        {
            _log.LogError(err, "export streaming failed for {Table}", table);
            return new { error = err.Message };
        }

        var downloadName = $"{table}.csv";
        var presignedUrl = await _minio.GetPresignedUrlAsync(
            key, _minioPublicUrl, expiresMinutes: 60,
            downloadFilename: downloadName,
            contentType: "text/csv; charset=utf-8",
            ct: ct);

        _log.LogInformation(
            "[export] {Table} window=[{S},{E}] key={Key} → presigned (60m)",
            table, startMs, endMs, key);

        return new { presigned_url = presignedUrl };
    }

    private async Task<object> HandleTableSchemaAsync(JsonElement p, CancellationToken ct)
    {
        var table = TryGetString(p, "table");
        if (string.IsNullOrEmpty(table)) return new { error = "missing field: table" };

        var schema = await _repo.ReadTableSchemaAsync(table, ct);
        return new { schema };
    }

    private static object HandleNormalizeTimeframe(JsonElement p)
    {
        var tf = TryGetString(p, "timeframe");
        if (string.IsNullOrEmpty(tf)) return new { error = "missing field: timeframe" };

        try
        {
            var (key, interval, stepMs) = DatasetCore.NormalizeTimeframe(tf);
            return new { key, interval, step_ms = stepMs };
        }
        catch (ArgumentException ex) { return new { error = ex.Message }; }
    }

    private static object HandleMakeTableName(JsonElement p)
    {
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe))
            return new { error = "missing fields: symbol, timeframe" };

        return new { table = DatasetCore.MakeTableName(symbol, timeframe) };
    }

    private async Task<object> HandleInstrumentDetailsAsync(JsonElement p, CancellationToken ct)
    {
        var category = TryGetString(p, "category");
        var symbol   = TryGetString(p, "symbol");
        if (string.IsNullOrEmpty(category) || string.IsNullOrEmpty(symbol))
            return new { error = "missing fields: category, symbol" };

        var (launchMs, fundingMs) = await _bybit.FetchInstrumentDetailsAsync(category, symbol, ct);
        return new { launch_ms = launchMs, funding_interval_ms = fundingMs };
    }

    private static object HandleConstants() => new
    {
        timeframes        = DatasetConstants.Timeframes.Keys,
        timeframe_aliases = DatasetConstants.TimeframeAliases,
        page_limit_kline  = DatasetConstants.PageLimitKline,
    };

    // ── Ingest pipeline ───────────────────────────────────────────────────

    /// <summary>
    /// Publishes a staged progress event on <see cref="Topics.EvtDataIngestProgress"/>.
    /// Fire-and-forget, errors swallowed inside the producer.
    /// </summary>
    private Task PublishIngestProgressAsync(
        string correlationId, string stage, string label,
        string status, int progress, string? detail, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(correlationId)) return Task.CompletedTask;
        var payload = new
        {
            correlation_id = correlationId,
            stage,
            label,
            status,
            progress,
            detail,
        };
        return _producer.PublishEventAsync(Topics.EvtDataIngestProgress, payload, ct);
    }

    private async Task<object> HandleIngestAsync(
        JsonElement p, string correlationId, CancellationToken ct)
    {
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        var startMs   = TryGetInt64(p, "start_ms");
        var endMs     = TryGetInt64(p, "end_ms");
        if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe)
            || startMs is null || endMs is null)
        {
            return new { error = "missing fields: symbol, timeframe, start_ms, end_ms" };
        }

        string? currentStage = null;
        string? currentLabel = null;

        try
        {
            string key, interval;
            long stepMs;
            (key, interval, stepMs) = DatasetCore.NormalizeTimeframe(timeframe);
            var (s, e) = DatasetCore.NormalizeWindow(startMs.Value, endMs.Value, stepMs);
            var table = DatasetCore.MakeTableName(symbol, key);

            // ── Stage: prepare ────────────────────────────────────────────
            currentStage = "prepare"; currentLabel = "Подготовка таблицы";
            await PublishIngestProgressAsync(correlationId, currentStage, currentLabel,
                "running", 0, $"table={table}", ct);

            await _repo.CreateTableIfNotExistsAsync(table, ct);
            var missing = await _repo.FindMissingTimestampsAsync(table, s, e, stepMs, ct);

            await PublishIngestProgressAsync(correlationId, currentStage, currentLabel,
                "done", 100, $"missing={missing.Count}", ct);

            if (missing.Count == 0)
                return new { status = "ok", rows_ingested = 0, table };

            // RSI needs warmup candles (Wilder, period 14) before the requested window.
            const int rsiPeriod = 14;
            var warmupCandles = Math.Max(DatasetConstants.DefaultWarmupCandles, rsiPeriod * 2);
            var fetchStart = s - warmupCandles * stepMs;
            var (oiLabel, oiIntervalMs) = DatasetCore.ChooseOpenInterestInterval(stepMs);

            // Incremental fetch range — only load OI/funding covering the
            // missing slice (plus one step back as a forward-fill buffer).
            // Klines still need full [fetchStart, e] because RSI requires
            // warmup candles before the requested window.
            const long fundingIntervalMs = 28_800_000L; // 8h — Bybit USDT perp default
            var missingStart = missing[0];
            var missingEnd   = missing[^1];
            var fetchOiStart      = missingStart - oiIntervalMs;
            var fetchFundingStart = missingStart - fundingIntervalMs;

            // ── Stage: fetch_klines (with per-page progress callback) ────
            const string klinesStage = "fetch_klines";
            const string klinesLabel = "Загрузка свечей";
            await PublishIngestProgressAsync(correlationId, klinesStage, klinesLabel,
                "running", 0, null, ct);

            var lastPublishedPage = 0;
            var klineTask = _bybit.FetchKlinesAsync(
                symbol.ToUpperInvariant(), interval, fetchStart, e, stepMs, 0, ct,
                onPageDone: (done, total) =>
                {
                    // Throttle: publish at most once every 10 pages (or on the last page).
                    if (done != total && done - lastPublishedPage < 10) return;
                    lastPublishedPage = done;
                    var pct = total > 0 ? (int)Math.Min(99, (long)done * 100 / total) : 0;
                    // fire-and-forget; completion publishes "done" after Task.WhenAll.
                    _ = PublishIngestProgressAsync(correlationId, klinesStage, klinesLabel,
                        "running", pct, $"{done} / {total} страниц", CancellationToken.None);
                });

            // ── Stage: fetch_funding ─────────────────────────────────────
            const string fundingStage = "fetch_funding";
            const string fundingLabel = "Загрузка funding rate";
            await PublishIngestProgressAsync(correlationId, fundingStage, fundingLabel,
                "running", 0, null, ct);
            var fundingTask = _bybit.FetchFundingRatesAsync(
                symbol.ToUpperInvariant(), fetchFundingStart, missingEnd, fundingIntervalMs, ct);

            // ── Stage: fetch_oi ──────────────────────────────────────────
            const string oiStage = "fetch_oi";
            const string oiLabelText = "Загрузка open interest";
            await PublishIngestProgressAsync(correlationId, oiStage, oiLabelText,
                "running", 0, null, ct);
            var oiTask = _bybit.FetchOpenInterestAsync(
                symbol.ToUpperInvariant(), oiLabel, fetchOiStart, missingEnd, oiIntervalMs, ct);

            // Await each independently so we can emit "done" per stage.
            var klines = await klineTask;
            await PublishIngestProgressAsync(correlationId, klinesStage, klinesLabel,
                "done", 100, $"{klines.Count} свечей", ct);

            var funding = await fundingTask;
            await PublishIngestProgressAsync(correlationId, fundingStage, fundingLabel,
                "done", 100, $"{funding.Count} записей", ct);

            var oi = await oiTask;
            await PublishIngestProgressAsync(correlationId, oiStage, oiLabelText,
                "done", 100, $"{oi.Count} записей", ct);

            // Index klines by timestamp for O(1) lookup.
            var klinesByTs = klines.ToDictionary(k => k.TimestampMs, k => k);

            // ── Stage: compute_rsi ───────────────────────────────────────
            const string rsiStage = "compute_rsi";
            const string rsiLabel = "Вычисление RSI";
            await PublishIngestProgressAsync(correlationId, rsiStage, rsiLabel,
                "running", 0, null, ct);

            // ComputeWilderRsiAsync expects (TimestampMs, Close) pairs — extract from full klines.
            var klineCloses = klines.Select(k => (k.TimestampMs, k.Close)).ToList();
            var rsiByTs = await ComputeWilderRsiAsync(
                klineCloses, rsiPeriod,
                onSegmentDone: (done, total) =>
                    PublishIngestProgressAsync(correlationId, rsiStage, rsiLabel,
                        "running", total > 0 ? (int)Math.Min(99, (long)done * 100 / total) : 0,
                        $"{done} / {total} сегментов", CancellationToken.None));

            await PublishIngestProgressAsync(correlationId, rsiStage, rsiLabel,
                "done", 100, $"{rsiByTs.Count} значений", ct);

            // Forward-fill funding + OI to candle timestamps in the requested window.
            var fundingFfill = BuildForwardFill(funding);
            var oiFfill      = BuildForwardFill(oi);

            // Build MarketRow list only for timestamps that are missing.
            var exchange = "bybit";
            var rows = new List<DatasetRepository.MarketRow>(missing.Count);
            foreach (var ts in missing)
            {
                if (!klinesByTs.TryGetValue(ts, out var kline)) continue;
                decimal? fr = LookupForwardFill(fundingFfill, ts);
                decimal? op = LookupForwardFill(oiFfill,      ts);
                decimal? rs = rsiByTs.TryGetValue(ts, out var r) ? r : (decimal?)null;
                rows.Add(new DatasetRepository.MarketRow(
                    TimestampMs:  ts,
                    Symbol:       symbol.ToUpperInvariant(),
                    Exchange:     exchange,
                    Timeframe:    key,
                    IndexPrice:   kline.Close,
                    OpenPrice:    kline.Open,
                    HighPrice:    kline.High,
                    LowPrice:     kline.Low,
                    Volume:       kline.Volume,
                    Turnover:     kline.Turnover,
                    FundingRate:  fr,
                    OpenInterest: op,
                    Rsi:          rs));
            }

            // ── Stage: upsert ────────────────────────────────────────────
            const string upsertStage = "upsert";
            const string upsertLabel = "Запись в базу";
            await PublishIngestProgressAsync(correlationId, upsertStage, upsertLabel,
                "running", 0, $"{rows.Count} строк", ct);

            var written = await _repo.BulkUpsertAsync(table, rows, ct);

            await PublishIngestProgressAsync(correlationId, upsertStage, upsertLabel,
                "done", 100, $"{written} строк записано", ct);

            // ── Stage: compute_features (SQL window-function pass) ──────
            // Не fatal — если upsert прошёл, потеря feature-шага не должна
            // откатывать ingest. Ошибку публикуем как отдельный event и
            // прокидываем в reply через features_error.
            const string featStage = "compute_features";
            const string featLabel = "Вычисление признаков";
            await PublishIngestProgressAsync(correlationId, featStage, featLabel,
                "running", 0, null, ct);

            long featuresUpdated = 0;
            string? featuresError = null;
            try
            {
                featuresUpdated = await _repo.ComputeAndUpdateFeaturesAsync(table, ct);
                await PublishIngestProgressAsync(correlationId, featStage, featLabel,
                    "done", 100, $"{featuresUpdated} строк обновлено", ct);
            }
            catch (Exception fex)
            {
                featuresError = fex.Message;
                _log.LogError(fex, "compute_features failed for {Table}", table);
                await PublishIngestProgressAsync(correlationId, featStage, featLabel,
                    "error", 0, fex.Message, CancellationToken.None);
            }

            _log.LogInformation(
                "[ingest] {Table} window=[{S},{E}] missing={Missing} fetched_klines={K} funding={F} oi={OI} written={W} features_updated={Feat}",
                table, s, e, missing.Count, klines.Count, funding.Count, oi.Count, written, featuresUpdated);

            return new
            {
                status           = "ok",
                table,
                rows_ingested    = written,
                missing          = missing.Count,
                fetched_klines   = klines.Count,
                fetched_funding  = funding.Count,
                fetched_oi       = oi.Count,
                features_updated = featuresUpdated,
                features_error   = featuresError,
            };
        }
        catch (ArgumentException ex)
        {
            if (currentStage is not null)
                await PublishIngestProgressAsync(correlationId, currentStage, currentLabel ?? currentStage,
                    "error", 0, ex.Message, CancellationToken.None);
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "ingest failed for {Symbol} {Tf}", symbol, timeframe);
            if (currentStage is not null)
                await PublishIngestProgressAsync(correlationId, currentStage, currentLabel ?? currentStage,
                    "error", 0, ex.Message, CancellationToken.None);
            return new { error = ex.Message };
        }
    }

    // ── CSV import (admin-side Upload CSV button) ─────────────────────────
    //
    // Payload: { table: "btcusdt_5m", rows: [{ timestamp_utc: "...", ... }, ...] }
    //
    // Each row object must expose a `timestamp_utc` key (ISO-8601 string OR
    // unix milliseconds) and arbitrary columns matching the market-data schema
    // (symbol, exchange, timeframe, index_price, funding_rate, open_interest,
    // rsi). Missing symbol / exchange / timeframe fall back to values derived
    // from the table name ("btcusdt_5m" → symbol=BTCUSDT, timeframe=5m,
    // exchange=bybit). Rows without a parseable timestamp are skipped and
    // logged — we never abort the whole batch on a malformed row.
    //
    // Reply: { status, rows_imported, rows_skipped, table }
    private async Task<object> HandleImportCsvAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing field: table" };

        if (payload.ValueKind != JsonValueKind.Object
            || !payload.TryGetProperty("rows", out var rowsEl)
            || rowsEl.ValueKind != JsonValueKind.Array)
        {
            return new { error = "missing field: rows (array)" };
        }

        // Derive defaults from the table name: "{symbol}_{timeframe}".
        var (defaultSymbol, defaultTimeframe) = SplitTableName(table);
        const string defaultExchange = "bybit";

        var rows    = new List<DatasetRepository.MarketRow>(rowsEl.GetArrayLength());
        var skipped = 0;

        foreach (var r in rowsEl.EnumerateArray())
        {
            if (r.ValueKind != JsonValueKind.Object) { skipped++; continue; }

            var tsMs = ParseTimestampMs(r);
            if (tsMs is null)
            {
                skipped++;
                continue;
            }

            var symbol    = ReadCellString(r, "symbol")    ?? defaultSymbol;
            var exchange  = ReadCellString(r, "exchange")  ?? defaultExchange;
            var timeframe = ReadCellString(r, "timeframe") ?? defaultTimeframe;

            rows.Add(new DatasetRepository.MarketRow(
                TimestampMs:  tsMs.Value,
                Symbol:       (symbol    ?? "").ToUpperInvariant(),
                Exchange:     exchange   ?? defaultExchange,
                Timeframe:    timeframe  ?? defaultTimeframe ?? "",
                IndexPrice:   ReadCellDecimal(r, "index_price"),
                OpenPrice:    ReadCellDecimal(r, "open_price"),
                HighPrice:    ReadCellDecimal(r, "high_price"),
                LowPrice:     ReadCellDecimal(r, "low_price"),
                Volume:       ReadCellDecimal(r, "volume"),
                Turnover:     ReadCellDecimal(r, "turnover"),
                FundingRate:  ReadCellDecimal(r, "funding_rate"),
                OpenInterest: ReadCellDecimal(r, "open_interest"),
                Rsi:          ReadCellDecimal(r, "rsi")));
        }

        if (skipped > 0)
        {
            _log.LogWarning(
                "[import_csv] {Table}: skipped {Skipped} row(s) with missing/invalid timestamp_utc",
                table, skipped);
        }

        if (rows.Count == 0)
            return new { status = "ok", table, rows_imported = 0L, rows_skipped = skipped };

        try
        {
            await _repo.CreateTableIfNotExistsAsync(table, ct);
            var written = await _repo.BulkUpsertAsync(table, rows, ct);
            _log.LogInformation(
                "[import_csv] {Table} imported={Written} skipped={Skipped}",
                table, written, skipped);
            return new
            {
                status        = "ok",
                table,
                rows_imported = written,
                rows_skipped  = skipped,
            };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "import_csv failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    private static (string? Symbol, string? Timeframe) SplitTableName(string table)
    {
        var idx = table.LastIndexOf('_');
        if (idx <= 0 || idx == table.Length - 1) return (null, null);
        return (table[..idx], table[(idx + 1)..]);
    }

    /// <summary>
    /// Parse `timestamp_utc` from a CSV row. Accepts either an ISO-8601 string
    /// or unix milliseconds (as a JSON number OR string of digits). Returns
    /// <c>null</c> for malformed / missing values.
    /// </summary>
    private static long? ParseTimestampMs(JsonElement row)
    {
        if (!row.TryGetProperty("timestamp_utc", out var v)) return null;
        switch (v.ValueKind)
        {
            case JsonValueKind.Number:
                if (v.TryGetInt64(out var asLong)) return asLong;
                if (v.TryGetDouble(out var asDouble)) return (long)asDouble;
                return null;
            case JsonValueKind.String:
                var s = v.GetString();
                if (string.IsNullOrWhiteSpace(s)) return null;
                if (long.TryParse(s, System.Globalization.NumberStyles.Integer,
                                  System.Globalization.CultureInfo.InvariantCulture, out var ms))
                {
                    return ms;
                }
                if (DateTimeOffset.TryParse(s, System.Globalization.CultureInfo.InvariantCulture,
                                            System.Globalization.DateTimeStyles.AssumeUniversal
                                          | System.Globalization.DateTimeStyles.AdjustToUniversal,
                                            out var dto))
                {
                    return dto.ToUnixTimeMilliseconds();
                }
                return null;
            default:
                return null;
        }
    }

    /// <summary>Read a string cell, tolerating JSON numbers/bools and empty strings.</summary>
    private static string? ReadCellString(JsonElement row, string key)
    {
        if (!row.TryGetProperty(key, out var v)) return null;
        return v.ValueKind switch
        {
            JsonValueKind.String => string.IsNullOrEmpty(v.GetString()) ? null : v.GetString(),
            JsonValueKind.Number => v.ToString(),
            JsonValueKind.True   => "true",
            JsonValueKind.False  => "false",
            _                    => null,
        };
    }

    /// <summary>Read a decimal cell. Empty strings / invalid → null (leaves column NULL).</summary>
    private static decimal? ReadCellDecimal(JsonElement row, string key)
    {
        if (!row.TryGetProperty(key, out var v)) return null;
        switch (v.ValueKind)
        {
            case JsonValueKind.Number:
                return v.TryGetDecimal(out var d) ? d : null;
            case JsonValueKind.String:
                var s = v.GetString();
                if (string.IsNullOrWhiteSpace(s)) return null;
                return decimal.TryParse(s, System.Globalization.NumberStyles.Float,
                                        System.Globalization.CultureInfo.InvariantCulture, out var parsed)
                    ? parsed
                    : null;
            default:
                return null;
        }
    }

    private async Task<object> HandleDeleteRowsAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing field: table" };

        var startMs = TryGetInt64(payload, "start_ms");
        var endMs   = TryGetInt64(payload, "end_ms");

        try
        {
            var deleted = await _repo.DeleteRowsAsync(table, startMs, endMs, ct);
            _log.LogInformation(
                "[delete_rows] {Table} range=[{S},{E}] deleted={Count}",
                table, startMs, endMs, deleted);
            return new { status = "ok", table, rows_deleted = deleted };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "delete_rows failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    // ── Anomaly / Inspect handlers ────────────────────────────────────────

    private async Task<object> HandleColumnStatsAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing field: table" };
        try
        {
            var stats = await _repo.GetColumnStatsAsync(table, ct);
            if (stats is null) return new { error = "table not found" };

            var total = stats.TotalRows;
            var cols = stats.Columns.Select(c => new
            {
                name       = c.Name,
                dtype      = c.Dtype,
                non_null   = c.NonNull,
                null_count = total - c.NonNull,
                null_pct   = total > 0 ? (double)(total - c.NonNull) * 100.0 / total : 0.0,
                min        = c.Min,
                max        = c.Max,
                mean       = c.Mean,
                std        = c.Std,
            }).ToList();

            return new { table, total_rows = total, columns = cols };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "column_stats failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    private async Task<object> HandleColumnHistogramAsync(JsonElement payload, CancellationToken ct)
    {
        var table  = TryGetString(payload, "table");
        var column = TryGetString(payload, "column");
        if (string.IsNullOrWhiteSpace(table))  return new { error = "missing field: table" };
        if (string.IsNullOrWhiteSpace(column)) return new { error = "missing field: column" };
        var buckets = (int)(TryGetInt64(payload, "buckets") ?? 30L);

        try
        {
            var hist = await _repo.GetColumnHistogramAsync(table, column, buckets, ct);
            if (hist is null) return new { error = "table not found" };

            return new
            {
                column  = hist.Column,
                min     = hist.Min,
                max     = hist.Max,
                buckets = hist.Buckets.Select(b => new
                {
                    range_start = b.RangeStart,
                    range_end   = b.RangeEnd,
                    count       = b.Count,
                }),
            };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "column_histogram failed for {Table}.{Column}", table, column);
            return new { error = ex.Message };
        }
    }

    // ── Browse (paginated raw rows) ───────────────────────────────────────

    private async Task<object> HandleBrowseAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        if (string.IsNullOrWhiteSpace(table)) return new { error = "missing field: table" };

        var page     = (int)(TryGetInt64(payload, "page")      ?? 0L);
        var pageSize = (int)(TryGetInt64(payload, "page_size") ?? 50L);
        var orderStr = TryGetString(payload, "order") ?? "desc";
        bool orderDesc = !string.Equals(orderStr, "asc", StringComparison.OrdinalIgnoreCase);

        if (page < 0) page = 0;

        try
        {
            var (total, rows) = await _repo.BrowseRowsAsync(table, page, pageSize, orderDesc, ct);
            return new { table, page, page_size = pageSize, total_rows = total, rows };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "browse failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    // ── Compute features (SQL window-function pass) ────────────────────────

    private async Task<object> HandleComputeFeaturesAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        if (string.IsNullOrWhiteSpace(table)) return new { error = "missing field: table" };

        try
        {
            var rowsUpdated = await _repo.ComputeAndUpdateFeaturesAsync(table, ct);
            return new { status = "ok", table, rows_updated = rowsUpdated };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "compute_features failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    /// <summary>
    /// Parallel Wilder's RSI (period N).
    ///
    /// Wilder's smoothing is recursive — each value depends on the previous
    /// <c>avgGain</c>/<c>avgLoss</c>. To parallelise safely we do a single
    /// cheap sequential pass to compute the exact smoothing state at the
    /// start of each segment (seed), then fan out: each worker finishes its
    /// own index range starting from its pre-computed seed, producing the
    /// same values as the sequential algorithm.
    /// </summary>
    private static async Task<Dictionary<long, decimal>> ComputeWilderRsiAsync(
        IReadOnlyList<(long TimestampMs, decimal Close)> klines,
        int period,
        Func<int, int, Task>? onSegmentDone = null)
    {
        var result = new Dictionary<long, decimal>();
        if (klines.Count <= period) return result;

        // 1. Seed the algorithm with the simple average of the first `period`
        //    gains/losses, and emit the first RSI value at index `period`.
        decimal gainSum = 0m, lossSum = 0m;
        for (int i = 1; i <= period; i++)
        {
            var diff = klines[i].Close - klines[i - 1].Close;
            if (diff >= 0) gainSum += diff; else lossSum += -diff;
        }
        decimal avgGain = gainSum / period;
        decimal avgLoss = lossSum / period;
        result[klines[period].TimestampMs] = avgLoss == 0
            ? 100m
            : 100m - 100m / (1m + avgGain / avgLoss);

        // Index range [first, last] that still needs computing: (period, klines.Count - 1].
        int first = period + 1;
        int last  = klines.Count - 1;
        int work  = last - first + 1;
        if (work <= 0)
        {
            if (onSegmentDone is not null) await onSegmentDone(1, 1);
            return result;
        }

        // 2. Choose a segment count based on CPU and work size.
        int segCount = Math.Clamp(Environment.ProcessorCount, 2, 8);
        segCount = Math.Min(segCount, work);
        if (segCount <= 1)
        {
            // Degrade to sequential for very small ranges.
            for (int i = first; i <= last; i++)
            {
                var diff = klines[i].Close - klines[i - 1].Close;
                var gain = diff > 0 ?  diff : 0m;
                var loss = diff < 0 ? -diff : 0m;
                avgGain = (avgGain * (period - 1) + gain) / period;
                avgLoss = (avgLoss * (period - 1) + loss) / period;
                result[klines[i].TimestampMs] = avgLoss == 0
                    ? 100m
                    : 100m - 100m / (1m + avgGain / avgLoss);
            }
            if (onSegmentDone is not null) await onSegmentDone(1, 1);
            return result;
        }

        // Compute segment boundaries: equal-ish slices of [first, last].
        var bounds = new (int From, int To)[segCount];
        {
            int chunk = work / segCount;
            int rem   = work % segCount;
            int cursor = first;
            for (int si = 0; si < segCount; si++)
            {
                int size = chunk + (si < rem ? 1 : 0);
                bounds[si] = (cursor, cursor + size - 1);
                cursor += size;
            }
        }

        // 3. Sequential "warm" pass — compute the exact (avgGain, avgLoss)
        //    state at the start of each segment. Arithmetic only, O(n).
        var seeds = new (decimal AvgGain, decimal AvgLoss)[segCount];
        seeds[0] = (avgGain, avgLoss);
        {
            decimal g = avgGain, l = avgLoss;
            int nextSeg = 1;
            for (int i = first; i <= last && nextSeg < segCount; i++)
            {
                var diff = klines[i].Close - klines[i - 1].Close;
                var gain = diff > 0 ?  diff : 0m;
                var loss = diff < 0 ? -diff : 0m;
                g = (g * (period - 1) + gain) / period;
                l = (l * (period - 1) + loss) / period;
                if (i + 1 == bounds[nextSeg].From)
                {
                    seeds[nextSeg] = (g, l);
                    nextSeg++;
                }
            }
        }

        // 4. Fan out: each worker computes its own slice and returns the
        //    partial dictionary. No shared state writes.
        int completed = 0;
        var partials = new Dictionary<long, decimal>[segCount];
        var workers = new Task[segCount];
        for (int si = 0; si < segCount; si++)
        {
            int idx = si;
            var (from, to) = bounds[idx];
            var (seedGain, seedLoss) = seeds[idx];
            workers[idx] = Task.Run(() =>
            {
                var local = new Dictionary<long, decimal>(to - from + 1);
                decimal g = seedGain, l = seedLoss;
                for (int i = from; i <= to; i++)
                {
                    var diff = klines[i].Close - klines[i - 1].Close;
                    var gain = diff > 0 ?  diff : 0m;
                    var loss = diff < 0 ? -diff : 0m;
                    g = (g * (period - 1) + gain) / period;
                    l = (l * (period - 1) + loss) / period;
                    local[klines[i].TimestampMs] = l == 0
                        ? 100m
                        : 100m - 100m / (1m + g / l);
                }
                partials[idx] = local;

                if (onSegmentDone is not null)
                {
                    var done = Interlocked.Increment(ref completed);
                    try { _ = onSegmentDone(done, segCount); } catch { /* ignore */ }
                }
            });
        }
        await Task.WhenAll(workers);

        // 5. Merge partials.
        foreach (var part in partials)
            foreach (var kv in part) result[kv.Key] = kv.Value;

        return result;
    }

    /// <summary>
    /// Sorted array of (timestampMs, value) used by <see cref="LookupForwardFill"/>.
    /// Input is assumed sorted ascending.
    /// </summary>
    private static (long[] Ts, decimal[] Vals) BuildForwardFill(
        IReadOnlyList<(long TimestampMs, decimal Value)> series)
    {
        var ts = new long[series.Count];
        var vs = new decimal[series.Count];
        for (int i = 0; i < series.Count; i++)
        {
            ts[i] = series[i].TimestampMs;
            vs[i] = series[i].Value;
        }
        return (ts, vs);
    }

    /// <summary>
    /// Forward-fill: return the value with the greatest timestamp &lt;= target.
    /// Returns null when target precedes the first observation.
    /// </summary>
    private static decimal? LookupForwardFill(
        (long[] Ts, decimal[] Vals) ffill, long targetMs)
    {
        var idx = Array.BinarySearch(ffill.Ts, targetMs);
        if (idx >= 0) return ffill.Vals[idx];
        idx = ~idx - 1;                       // largest < target
        if (idx < 0) return null;
        return ffill.Vals[idx];
    }

    public override void Dispose()
    {
        _consumer.Dispose();
        base.Dispose();
    }
}
