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
    // ── Anomaly detection / clean handlers ────────────────────────────────

    /// <summary>
    /// Run all anomaly checks in parallel and return a summary-first response.
    ///
    /// Response shape (always):
    ///   table, total, critical, warning, by_type, sample (≤ AnomalyInlineRowSample rows),
    ///   page, page_size, has_more, [optional] report_url.
    ///
    /// Behaviour:
    ///   - When <c>page</c>/<c>page_size</c> are supplied the requested
    ///     window is returned in <c>rows</c> (sorted by timestamp asc).
    ///   - Otherwise the response carries only <c>sample</c> (top
    ///     critical-first then chronological), with <c>has_more</c> set when
    ///     <c>total</c> exceeds the inline cap.
    ///   - When the full result exceeds <c>AnomalyInlineRowSample</c> rows,
    ///     a JSON report of every detection is uploaded to MinIO and a
    ///     presigned URL is returned in <c>report_url</c>. The UI is
    ///     expected to show the summary + sample inline and let the user
    ///     download the full report from the URL.
    /// </summary>
    private async Task<object> HandleDetectAnomaliesAsync(JsonElement p, CancellationToken ct)
    {
        var table  = TryGetString(p, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing required field: table" };
        var stepMs = TryGetInt64(p, "step_ms") ?? 0;
        var z      = p.TryGetProperty("z_threshold", out var zEl)
                      && zEl.ValueKind == JsonValueKind.Number
                      ? zEl.GetDouble() : 3.0;

        // ── New optional parameters for the four extra anomaly types ──
        // All four can be turned off independently by passing the corresponding
        // *_enabled = false. By default they're all on for backwards-compat —
        // the old front-end will simply receive a richer reply.
        bool RollingEnabled  = !p.TryGetProperty("rolling_enabled",  out var re) || re.ValueKind != JsonValueKind.False;
        bool StaleEnabled    = !p.TryGetProperty("stale_enabled",    out var se) || se.ValueKind != JsonValueKind.False;
        bool ReturnEnabled   = !p.TryGetProperty("return_enabled",   out var rne) || rne.ValueKind != JsonValueKind.False;
        bool VolMismatchEnabled = !p.TryGetProperty("volmismatch_enabled", out var vme) || vme.ValueKind != JsonValueKind.False;

        var rollingCol    = TryGetString(p, "rolling_column") ?? "close_price";
        var rollingWindow = (int)(TryGetInt64(p, "rolling_window") ?? 96);
        var rollingThr    = p.TryGetProperty("rolling_threshold", out var rtEl)
                              && rtEl.ValueKind == JsonValueKind.Number
                              ? rtEl.GetDouble() : 4.5;
        var rollingMode   = TryGetString(p, "rolling_mode") ?? "zscore"; // zscore|iqr

        var staleCol    = TryGetString(p, "stale_column") ?? "close_price";
        var staleMinLen = (int)(TryGetInt64(p, "stale_min_len") ?? 5);

        var returnCol   = TryGetString(p, "return_column") ?? "close_price";
        var returnThr   = p.TryGetProperty("return_threshold_pct", out var rtpEl)
                            && rtpEl.ValueKind == JsonValueKind.Number
                            ? rtpEl.GetDouble() : 15.0;

        var volTol      = p.TryGetProperty("volmismatch_tolerance_pct", out var vtEl)
                            && vtEl.ValueKind == JsonValueKind.Number
                            ? vtEl.GetDouble() : 5.0;

        try
        {
            // Inner semaphore: cap the fan-out to 5 concurrent SQL connections
            // per anomaly run. With _heavyConcurrency(4) that gives at most
            // 4 × 5 = 20 connections from anomaly handlers, well within the
            // MaxPoolSize=25 budget and leaving 5 slots for light handlers.
            using var inner = new SemaphoreSlim(5, 5);
            async Task<IReadOnlyList<DatasetRepository.AnomalyRow>> Guarded(
                Func<Task<IReadOnlyList<DatasetRepository.AnomalyRow>>> factory)
            {
                await inner.WaitAsync(ct);
                try   { return await factory(); }
                finally { inner.Release(); }
            }

            var Empty = Task.FromResult<IReadOnlyList<DatasetRepository.AnomalyRow>>(
                Array.Empty<DatasetRepository.AnomalyRow>());

            var gapsTask     = stepMs > 0
                ? Guarded(() => _repo.DetectGapsAsync(table, stepMs, ct))
                : Empty;
            var dupTask      = Guarded(() => _repo.DetectDuplicatesAsync(table, ct));
            var ohlcTask     = Guarded(() => _repo.DetectOhlcViolationsAsync(table, ct));
            var negTask      = Guarded(() => _repo.DetectNegativesAsync(table, ct));
            var streakTask   = Guarded(() => _repo.DetectZeroStreaksAsync(table, 3, ct));
            var outlierTask  = Guarded(() => _repo.DetectStatisticalOutliersAsync(table, z, ct));

            // The four new structural detectors. We resolve each conditionally
            // to an empty list when disabled so Task.WhenAll stays cheap.
            var rollingTask = RollingEnabled
                ? Guarded(() => _repo.DetectRollingZScoreAsync(table, rollingCol, rollingWindow, rollingThr, rollingMode, ct))
                : Empty;
            var staleTask   = StaleEnabled
                ? Guarded(() => _repo.DetectStalePriceAsync(table, staleCol, staleMinLen, ct))
                : Empty;
            var retTask     = ReturnEnabled
                ? Guarded(() => _repo.DetectReturnOutliersAsync(table, returnCol, returnThr, ct))
                : Empty;
            var volMismatchTask = VolMismatchEnabled
                ? Guarded(() => _repo.DetectVolumeMismatchAsync(table, volTol, ct))
                : Empty;

            await Task.WhenAll(gapsTask, dupTask, ohlcTask, negTask, streakTask, outlierTask,
                               rollingTask, staleTask, retTask, volMismatchTask);

            var all = new List<DatasetRepository.AnomalyRow>();
            all.AddRange(gapsTask.Result);
            all.AddRange(dupTask.Result);
            all.AddRange(ohlcTask.Result);
            all.AddRange(negTask.Result);
            all.AddRange(streakTask.Result);
            all.AddRange(outlierTask.Result);
            all.AddRange(rollingTask.Result);
            all.AddRange(staleTask.Result);
            all.AddRange(retTask.Result);
            all.AddRange(volMismatchTask.Result);

            var byType = all.GroupBy(r => r.AnomalyType)
                            .ToDictionary(g => g.Key, g => (long)g.Count());
            var critical = all.Count(r => r.Severity == "critical");
            var warning  = all.Count(r => r.Severity == "warning");
            var total    = all.Count;

            // ── Pagination over the full chronological list ──────────────
            // The caller can ask for a slice via { page, page_size } —
            // typical UI flow: first request returns summary + sample; user
            // clicks "next page" → same command with explicit pagination.
            int? page     = (int?)TryGetInt64(p, "page");
            int  pageSize = (int)(TryGetInt64(p, "page_size") ?? 0L);
            object? rowsSlice = null;
            bool hasMore   = total > AnomalyInlineRowSample;

            // Pre-sort once — used by both pagination and the sample.
            var sortedByTs = all.OrderBy(r => r.TsMs).ToList();

            if (page is int pg && pageSize > 0)
            {
                pg = Math.Max(0, pg);
                pageSize = Math.Clamp(pageSize, 1, 5_000);
                var slice = sortedByTs
                    .Skip(pg * pageSize)
                    .Take(pageSize)
                    .Select(MapAnomaly)
                    .ToArray();
                hasMore = (long)(pg + 1) * pageSize < total;
                rowsSlice = slice;
            }

            // ── Sample for summary-first response ────────────────────────
            // Critical first (so the UI's first impression is "what
            // matters"), then chronological inside each severity tier.
            var sample = all
                .OrderByDescending(r => r.Severity == "critical" ? 1 : 0)
                .ThenByDescending(r => r.Severity == "warning" ? 1 : 0)
                .ThenBy(r => r.TsMs)
                .Take(AnomalyInlineRowSample)
                .Select(MapAnomaly)
                .ToArray();

            // ── Claim-check: full report → MinIO when total is large ─────
            string? reportUrl = null;
            if (total > AnomalyInlineRowSample)
            {
                try
                {
                    var key = $"reports/anomaly_{Guid.NewGuid():N}.json";
                    // True streaming: write JSON directly into MinIO via a
                    // Pipe using Utf8JsonWriter. We never materialise the row
                    // array — `sortedByTs` is iterated lazily and each
                    // anomaly is emitted to the writer one element at a
                    // time. Peak memory is bounded to the pipe's internal
                    // buffer plus the JsonWriter's flush threshold
                    // (≈ tens of KB) regardless of total row count.
                    var pipe = new Pipe();
                    var serializeTask = Task.Run(async () =>
                    {
                        try
                        {
                            await using var ws = pipe.Writer.AsStream(leaveOpen: false);
                            await using var writer = new Utf8JsonWriter(ws);
                            writer.WriteStartObject();
                            writer.WriteString("table", table);
                            writer.WriteNumber("total", total);
                            writer.WriteNumber("critical", critical);
                            writer.WriteNumber("warning", warning);
                            writer.WriteStartObject("by_type");
                            foreach (var kv in byType)
                                writer.WriteNumber(kv.Key, kv.Value);
                            writer.WriteEndObject();
                            writer.WriteStartArray("rows");
                            int sinceFlush = 0;
                            foreach (var r in sortedByTs)
                            {
                                writer.WriteStartObject();
                                writer.WriteNumber("ts_ms", r.TsMs);
                                writer.WriteString("anomaly_type", r.AnomalyType);
                                writer.WriteString("severity", r.Severity);
                                if (r.Column is null) writer.WriteNull("column");
                                else writer.WriteString("column", r.Column);
                                if (r.Value is double v) writer.WriteNumber("value", v);
                                else writer.WriteNull("value");
                                if (r.Details is null) writer.WriteNull("details");
                                else writer.WriteString("details", r.Details);
                                writer.WriteEndObject();
                                if (++sinceFlush >= 1024)
                                {
                                    await writer.FlushAsync(ct);
                                    sinceFlush = 0;
                                }
                            }
                            writer.WriteEndArray();
                            writer.WriteEndObject();
                            await writer.FlushAsync(ct);
                        }
                        catch (Exception ex)
                        {
                            await pipe.Writer.CompleteAsync(ex);
                        }
                    });
                    await Task.WhenAll(
                        _minio.PutStreamAsync(pipe.Reader.AsStream(), key, "application/json", ct),
                        serializeTask);
                    reportUrl = await _minio.GetPresignedUrlAsync(
                        key, _browserDownloadBaseUrl, expiresMinutes: 60,
                        downloadFilename: $"anomaly_{table}.json",
                        contentType: "application/json",
                        ct: ct);
                }
                catch (Exception ex)
                {
                    // Non-fatal: caller still gets the summary + sample.
                    _log.LogWarning(ex, "anomaly report upload failed for {Table}", table);
                }
            }

            return new
            {
                table,
                total,
                critical,
                warning,
                by_type    = byType,
                page       = page ?? 0,
                page_size  = pageSize,
                has_more   = hasMore,
                sample,
                rows       = rowsSlice,
                report_url = reportUrl,
            };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Anomaly detection failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    /// <summary>Project an <see cref="DatasetRepository.AnomalyRow"/> to the JSON shape the UI expects.</summary>
    private static object MapAnomaly(DatasetRepository.AnomalyRow r) => new
    {
        ts_ms        = r.TsMs,
        anomaly_type = r.AnomalyType,
        severity     = r.Severity,
        column       = r.Column,
        value        = r.Value,
        details      = r.Details,
    };

    /// <summary>
    /// Compute counts for each requested clean operation. Read-only.
    /// </summary>
    private async Task<object> HandleCleanPreviewAsync(JsonElement p, CancellationToken ct)
    {
        var table  = TryGetString(p, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing required field: table" };
        var stepMs = TryGetInt64(p, "step_ms") ?? 0;

        long deleteByTimestampsCount = 0;
        if (p.TryGetProperty("delete_timestamps", out var dtsEl)
            && dtsEl.ValueKind == JsonValueKind.Array)
        {
            deleteByTimestampsCount = dtsEl.GetArrayLength();
        }

        try
        {
            var dupTask    = _repo.CountDuplicatesAsync(table, ct);
            var ohlcTask   = _repo.CountOhlcViolationsAsync(table, ct);
            var streakTask = _repo.CountZeroStreakRowsAsync(table, 3, ct);
            var gapsTask   = stepMs > 0
                ? _repo.CountGapsAsync(table, stepMs, ct)
                : Task.FromResult(0L);

            await Task.WhenAll(dupTask, ohlcTask, streakTask, gapsTask);

            return new
            {
                table,
                counts = new
                {
                    drop_duplicates       = dupTask.Result,
                    fix_ohlc              = ohlcTask.Result,
                    fill_zero_streaks     = streakTask.Result,
                    delete_by_timestamps  = deleteByTimestampsCount,
                    fill_gaps             = gapsTask.Result,
                },
            };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Clean preview failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    /// <summary>
    /// Apply selected clean operations under an advisory lock keyed by table
    /// name, in the documented order. Requires <c>confirm: true</c>.
    /// </summary>
    private async Task<object> HandleCleanApplyAsync(JsonElement p, CancellationToken ct)
    {
        var table = TryGetString(p, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing required field: table" };

        var confirm = p.TryGetProperty("confirm", out var cEl)
                      && cEl.ValueKind == JsonValueKind.True;
        if (!confirm)
            return new { error = "operation requires confirm=true" };

        bool getBool(string n) => p.TryGetProperty(n, out var e)
                                   && e.ValueKind == JsonValueKind.True;

        var doDup    = getBool("drop_duplicates");
        var doOhlc   = getBool("fix_ohlc");
        var doStreak = getBool("fill_zero_streaks");
        var doDelete = getBool("delete_by_timestamps");
        var doGaps   = getBool("fill_gaps");

        var deleteTs = new List<long>();
        if (doDelete && p.TryGetProperty("delete_timestamps", out var dtsEl)
            && dtsEl.ValueKind == JsonValueKind.Array)
        {
            foreach (var t in dtsEl.EnumerateArray())
                if (t.ValueKind == JsonValueKind.Number && t.TryGetInt64(out var v))
                    deleteTs.Add(v);
        }

        var stepMs = TryGetInt64(p, "step_ms") ?? 0;
        var method = TryGetString(p, "interpolation_method") ?? "forward_fill";
        // "first" (default) | "last" | "none" — passed straight through to
        // ApplyDropDuplicatesAsync.
        var dedupStrategy = TryGetString(p, "dedup_strategy") ?? "first";

        // fill_gaps now also supports "drop" (delete the bordering rows around
        // each gap to remove the gap entirely instead of inserting synthetic
        // rows). When "drop", we delegate to a different code path.
        var fillGapsMethod = method; // alias for readability

        // fill_zero_streaks columns selector. Empty / "all" → both legacy
        // columns; otherwise we honour the comma-separated whitelist.
        var streakColsSel = TryGetString(p, "fill_zero_streaks_columns") ?? "all";
        var streakCols = streakColsSel == "all" || string.IsNullOrWhiteSpace(streakColsSel)
            ? new[] { "open_interest", "funding_rate" }
            : streakColsSel.Split(',', StringSplitOptions.RemoveEmptyEntries
                                    | StringSplitOptions.TrimEntries)
                .Where(c => c == "volume" || c == "open_interest"
                         || c == "funding_rate" || c == "turnover")
                .ToArray();

        await _repo.EnsureAuditLogAsync(ct);

        // Acquire advisory lock first to serialise concurrent applies.
        var conn = await _repo.AcquireApplyLockAsync(table, ct);
        var totals = new Dictionary<string, long>();
        try
        {
            // Order matters: dedupe → in-place fixes → deletes → gap fills.
            // This ensures fill_gaps sees a clean grid.
            if (doDup)
                totals["drop_duplicates"] = await _repo.ApplyDropDuplicatesAsync(
                    table, conn, dedupStrategy, ct);

            if (doOhlc)
                totals["fix_ohlc"] = await _repo.ApplyFixOhlcAsync(table, conn, ct);

            if (doStreak)
            {
                long sum = 0;
                foreach (var col in streakCols)
                {
                    try { sum += await _repo.ApplyFillZeroStreakAsync(table, col, conn, ct); }
                    catch (PostgresException) { /* column absent on this table */ }
                }
                totals["fill_zero_streaks"] = sum;
            }

            if (doDelete && deleteTs.Count > 0)
                totals["delete_by_timestamps"] =
                    await _repo.ApplyDeleteByTimestampsAsync(table, deleteTs, conn, ct);

            if (doGaps && stepMs > 0)
            {
                // "drop_rows" is the third UI option — semantically "do not
                // synthesise; keep gaps as-is". We treat it as a no-op so the
                // checkbox can still be ticked without polluting the table.
                if (string.Equals(fillGapsMethod, "drop_rows", StringComparison.OrdinalIgnoreCase))
                {
                    totals["fill_gaps"] = 0;
                }
                else
                {
                    totals["fill_gaps"] =
                        await _repo.ApplyFillGapsAsync(table, stepMs, fillGapsMethod, conn, ct);
                }
            }
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Clean apply failed for {Table}", table);
            await _repo.ReleaseApplyLockAsync(conn, table, ct);
            return new { error = ex.Message };
        }

        var totalRows = totals.Values.Sum();
        // WriteAuditLogAsync opens its own connection (it does not reuse `conn`),
        // so we wrap the audit + release pair in try/finally to guarantee the
        // advisory lock is released even if the audit-log INSERT throws.
        var paramsJson = JsonSerializer.Serialize(new
        {
            drop_duplicates           = doDup,
            fix_ohlc                  = doOhlc,
            fill_zero_streaks         = doStreak,
            delete_by_timestamps      = doDelete ? deleteTs.Count : 0,
            fill_gaps                 = doGaps,
            step_ms                   = stepMs,
            interpolation_method      = method,
            dedup_strategy            = dedupStrategy,
            fill_zero_streaks_columns = streakColsSel,
        });
        int auditId;
        try
        {
            auditId = await _repo.WriteAuditLogAsync(table, "clean.apply", paramsJson, totalRows, ct);
        }
        finally
        {
            await _repo.ReleaseApplyLockAsync(conn, table, ct);
        }

        return new
        {
            table,
            audit_id      = auditId,
            rows_affected = totals,
            total         = totalRows,
        };
    }

    /// <summary>
    /// Return the latest <c>limit</c> entries from the dataset_audit_log,
    /// optionally filtered by <c>table_name</c>. Backs the History tab on
    /// the Anomaly page.
    /// </summary>
    private async Task<object> HandleAuditLogAsync(JsonElement p, CancellationToken ct)
    {
        var table = TryGetString(p, "table");
        var limit = (int)(TryGetInt64(p, "limit") ?? 50);
        try
        {
            var entries = await _repo.GetAuditLogAsync(
                string.IsNullOrWhiteSpace(table) ? null : table, limit, ct);
            return new
            {
                entries = entries.Select(e => new
                {
                    id            = e.Id,
                    table_name    = e.TableName,
                    operation     = e.Operation,
                    @params       = e.ParamsJson,
                    rows_affected = e.RowsAffected,
                    applied_at_ms = new DateTimeOffset(
                        DateTime.SpecifyKind(e.AppliedAt, DateTimeKind.Utc))
                        .ToUnixTimeMilliseconds(),
                }).ToArray(),
            };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "audit_log query failed");
            return new { error = ex.Message, entries = Array.Empty<object>() };
        }
    }
}
