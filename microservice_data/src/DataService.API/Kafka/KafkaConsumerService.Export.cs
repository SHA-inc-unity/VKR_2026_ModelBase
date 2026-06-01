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

public sealed partial class KafkaConsumerService
{
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
        // The ZIP archive itself is also streamed: ZipArchive writes directly
        // into a pipe-backed writer stream while MinIO multipart-upload reads
        // from the pipe concurrently. No MemoryStream and no full ZIP buffer.
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

            // Pipe-based streaming pipeline — producer builds the ZIP sequentially
            // (one table at a time) writing directly into the pipe; consumer multipart-
            // uploads the pipe reader to MinIO.  Peak RAM: ~64 KB pipe buffer + one
            // 5 MB TransferUtility part, regardless of how many tables or rows are
            // exported.
            var symbol  = TryGetString(p, "symbol") ?? "export";
            var zipKey  = $"exports/{Guid.NewGuid():N}.zip";
            var zipPipe = new Pipe();

            Exception? zipProducerError = null;
            var zipProducerTask = Task.Run(async () =>
            {
                Exception? captured = null;
                try
                {
                    await using var writerStream = zipPipe.Writer.AsStream(leaveOpen: true);
                    using (var archive = new ZipArchive(
                        writerStream, ZipArchiveMode.Create, leaveOpen: true))
                    {
                        foreach (var tableName in tables)
                        {
                            var entry = archive.CreateEntry(
                                $"{tableName}.csv", CompressionLevel.Fastest);
                            await using var entryStream = entry.Open();
                            await _repo.ExportCsvToStreamAsync(
                                tableName, startMs.Value, endMs.Value, entryStream, ct);
                        }
                    } // archive.Dispose() flushes ZIP central directory into writerStream
                    await writerStream.FlushAsync(ct);
                }
                catch (Exception ex) { captured = ex; zipProducerError = ex; }
                await zipPipe.Writer.CompleteAsync(captured);
            }, ct);

            Exception? zipConsumerError = null;
            var zipConsumerTask = Task.Run(async () =>
            {
                Exception? captured = null;
                try
                {
                    await using var reader = zipPipe.Reader.AsStream(leaveOpen: true);
                    await _minio.PutStreamAsync(reader, zipKey, "application/zip", ct);
                }
                catch (Exception ex) { captured = ex; zipConsumerError = ex; }
                await zipPipe.Reader.CompleteAsync(captured);
            }, ct);

            await Task.WhenAll(zipProducerTask, zipConsumerTask);

            var zipErr = zipProducerError ?? zipConsumerError;
            if (zipErr is not null)
            {
                _log.LogError(zipErr,
                    "[export:zip] streaming failed tables={Tables}",
                    string.Join(",", tables));
                return new { error = zipErr.Message };
            }

            var zipDownloadName = $"{symbol}_ALL.zip";
            var zipPresignedUrl = await _minio.GetPresignedUrlAsync(
                zipKey, _browserDownloadBaseUrl, expiresMinutes: 60,
                downloadFilename: zipDownloadName,
                contentType: "application/zip",
                ct: ct);

            _log.LogInformation(
                "[export:zip] tables={Count} key={Key} → presigned (60m)",
                tables.Count, zipKey);

            return new { presigned_url = zipPresignedUrl };
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
            key, _browserDownloadBaseUrl, expiresMinutes: 60,
            downloadFilename: downloadName,
            contentType: "text/csv; charset=utf-8",
            ct: ct);

        _log.LogInformation(
            "[export] {Table} window=[{S},{E}] key={Key} → presigned (60m)",
            table, startMs, endMs, key);

        return new { presigned_url = presignedUrl };
    }

    /// <summary>
    /// Composite "load this whole dataset" command for downstream services
    /// (currently microservice_analitic). Replaces the three-call dance of
    /// make_table → coverage → export with a single Kafka round-trip:
    ///
    ///   request:  { symbol, timeframe, max_rows? }
    ///   response: { table_name, row_count, presigned_url }      // success
    ///             { error: "table_not_found" | "empty_table"
    ///                    | "row_count_exceeds_limit", row_count?, limit? }
    ///
    /// Internally it resolves the table name via DatasetCore, looks up
    /// coverage, enforces the optional row-count cap, then streams the full
    /// table to MinIO using the same pipe-based pipeline as
    /// <see cref="HandleExportAsync"/>. No alternative time-slice mode —
    /// callers that need a window keep using cmd.data.dataset.export.
    /// </summary>
    private async Task<object> HandleExportFullAsync(JsonElement p, CancellationToken ct)
    {
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        var exchange  = TryGetString(p, "exchange") ?? "bybit";
        if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe))
            return new { error = "missing fields: symbol, timeframe" };

        string table;
        try { table = DatasetCore.MakeTableName(symbol, timeframe, exchange); }
        catch (ArgumentException ex) { return new { error = ex.Message }; }

        var cov = await _repo.GetCoverageIfExistsAsync(table, ct);
        if (cov is null)
            return new { error = "table_not_found", table };

        var (rows, minTsMs, maxTsMs) = cov.Value;
        if (rows <= 0)
            return new { error = "empty_table", table };

        var maxRows = TryGetInt64(p, "max_rows");
        if (maxRows is long cap && cap > 0 && rows > cap)
            return new { error = "row_count_exceeds_limit", row_count = rows, limit = cap };

        // Same streaming pipeline as the time-slice export path: PostgreSQL
        // COPY → pipe → MinIO multipart upload. Peak memory is one pipe
        // buffer (~64 KB) plus one S3 part (~5 MB), regardless of row count.
        var key  = $"exports/{Guid.NewGuid():N}.csv";
        var pipe = new Pipe();

        Exception? producerError = null;
        var producerTask = Task.Run(async () =>
        {
            Exception? captured = null;
            try
            {
                await using var writer = pipe.Writer.AsStream(leaveOpen: true);
                await _repo.ExportCsvToStreamAsync(table, minTsMs, maxTsMs, writer, ct);
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
            _log.LogError(err, "[export_full] streaming failed for {Table}", table);
            return new { error = err.Message };
        }

        // export_full отдаётся другим микросервисам (microservice_analitic),
        // которые тянут CSV прямо из docker-сети. Там browser-facing nginx
        // недоступен, поэтому подписываем URL на внутренний `minio:9000`.
        var presignedUrl = await _minio.GetPresignedUrlAsync(
            key, _internalDownloadBaseUrl, expiresMinutes: 60,
            downloadFilename: $"{table}.csv",
            contentType: "text/csv; charset=utf-8",
            ct: ct);

        _log.LogInformation(
            "[export_full] {Table} rows={Rows} key={Key} → presigned (60m)",
            table, rows, key);

        return new
        {
            table_name    = table,
            row_count     = rows,
            presigned_url = presignedUrl,
        };
    }
}
