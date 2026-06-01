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
    private async Task<object> HandleCoverageAsync(JsonElement p, CancellationToken ct)
    {
        // Resolve table name from either explicit { table } or { symbol, timeframe }.
        var table = TryGetString(p, "table");
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        var exchange  = TryGetString(p, "exchange") ?? "bybit";
        long? stepMs = null;

        if (string.IsNullOrEmpty(table))
        {
            if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe))
                return new { error = "missing fields: table, or (symbol + timeframe)" };
            try
            {
                var (key, _, tfStep) = DatasetCore.NormalizeTimeframe(timeframe);
                stepMs = tfStep;
                table  = DatasetCore.MakeTableName(symbol, key, exchange);
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
        var includeRows = !p.TryGetProperty("include_rows", out var includeRowsEl) ||
            includeRowsEl.ValueKind != JsonValueKind.False;

        if (!includeRows)
        {
            var bounds = await _repo.GetBoundsIfExistsAsync(table, ct);
            if (bounds is null)
            {
                return new
                {
                    exists       = false,
                    table_name   = table,
                    rows         = 0L,
                    rows_known   = false,
                    expected     = 0L,
                    coverage_pct = (double?)null,
                    gaps         = (long?)null,
                    min_ts_ms    = (long?)null,
                    max_ts_ms    = (long?)null,
                    date_from    = (string?)null,
                    date_to      = (string?)null,
                };
            }

            return new
            {
                exists       = true,
                table_name   = table,
                rows         = 0L,
                rows_known   = false,
                expected     = 0L,
                coverage_pct = (double?)null,
                gaps         = (long?)null,
                min_ts_ms    = bounds.Value.MinTsMs,
                max_ts_ms    = bounds.Value.MaxTsMs,
                date_from    = FormatDate(bounds.Value.MinTsMs),
                date_to      = FormatDate(bounds.Value.MaxTsMs),
            };
        }

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
        long rowsForPct = cov.Value.Rows;
        bool rangeMode = false;
        if (stepMs is long step && step > 0)
        {
            if (startMs is not null && endMs is not null && endMs.Value > startMs.Value)
            {
                // Range-scoped coverage: use real row count inside the window
                // (Phase F) instead of the full-table count, which is what the
                // user actually asked about.
                var rng = await _repo.GetCoverageRangeAsync(
                    table, startMs.Value, endMs.Value, step, ct);
                if (rng is not null)
                {
                    expected = rng.Value.ExpectedInRange;
                    rowsForPct = rng.Value.RowsInRange;
                    rangeMode = true;
                }
                else
                {
                    expected = Math.Max(0, (endMs.Value - startMs.Value) / step + 1);
                }
            }
            else if (cov.Value.MaxTsMs > cov.Value.MinTsMs)
                expected = Math.Max(0, (cov.Value.MaxTsMs - cov.Value.MinTsMs) / step + 1);
        }

        double pct = expected > 0
            ? Math.Min(100.0, Math.Round((double)rowsForPct / expected * 100.0, 2))
            : 0.0;
        long gaps = expected > rowsForPct ? expected - rowsForPct : 0L;

        return new
        {
            exists       = true,
            table_name   = table,
            rows         = cov.Value.Rows,
            rows_in_range = rangeMode ? (long?)rowsForPct : null,
            min_ts_ms    = cov.Value.MinTsMs,
            max_ts_ms    = cov.Value.MaxTsMs,
            expected,
            coverage_pct = pct,
            gaps,
            range_mode   = rangeMode,
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
        var limit   = TryGetInt64(p, "limit");
        if (string.IsNullOrEmpty(table) || startMs is null || endMs is null)
            return new { error = "missing fields: table, start_ms, end_ms" };

        var columns = TryGetStringArray(p, "columns");

        var rows = await _repo.FetchRowsAsync(
            table,
            startMs.Value,
            endMs.Value,
            limit is long requestedLimit ? (int?)requestedLimit : null,
            columns,
            ct);
        var json = JsonSerializer.SerializeToUtf8Bytes(new { rows });
        if (json.Length > InlinePayloadLimit)
        {
            var claim = await _minio.PutBytesAsync(json, contentType: "application/json", ct: ct);
            return new { claim_check = claim };
        }
        return new { rows };
    }

    private async Task<object> HandleLatestRowsAsync(
        JsonElement p, string replyTo, string correlationId, CancellationToken ct)
    {
        var table  = TryGetString(p, "table");
        var stepMs = TryGetInt64(p, "step_ms");
        var limit  = TryGetInt64(p, "limit");
        if (string.IsNullOrEmpty(table) || stepMs is null || limit is null)
            return new { error = "missing fields: table, step_ms, limit" };

        if (stepMs.Value <= 0 || limit.Value <= 0)
            return new { error = "step_ms and limit must be positive" };

        var columns = TryGetStringArray(p, "columns");

        var rows = await _repo.FetchLatestWindowRowsAsync(
            table, stepMs.Value, (int)limit.Value, columns, ct);
        var json = JsonSerializer.SerializeToUtf8Bytes(new { rows });
        if (json.Length > InlinePayloadLimit)
        {
            var claim = await _minio.PutBytesAsync(json, contentType: "application/json", ct: ct);
            return new { claim_check = claim };
        }

        return new { rows };
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
        var exchange  = TryGetString(p, "exchange") ?? "bybit";
        if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe))
            return new { error = "missing fields: symbol, timeframe" };

        return new { table = DatasetCore.MakeTableName(symbol, timeframe, exchange) };
    }

    private async Task<object> HandleInstrumentDetailsAsync(JsonElement p, CancellationToken ct)
    {
        var category = TryGetString(p, "category");
        var symbol   = TryGetString(p, "symbol");
        var exchange = (TryGetString(p, "exchange") ?? "bybit").Trim().ToLowerInvariant();
        if (string.IsNullOrEmpty(category) || string.IsNullOrEmpty(symbol))
            return new { error = "missing fields: category, symbol" };

        var market = _markets.GetRequiredClient(exchange);
        var (launchMs, fundingMs) = await market.FetchInstrumentDetailsAsync(category, symbol, ct);
        return new { launch_ms = launchMs, funding_interval_ms = fundingMs };
    }

    private static object HandleConstants() => new
    {
        timeframes        = DatasetConstants.Timeframes.Keys,
        timeframe_aliases = DatasetConstants.TimeframeAliases,
        page_limit_kline  = DatasetConstants.PageLimitKline,
    };

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

        // Optional: restrict to a specific set of columns (e.g. quality audit
        // only needs 16 columns, not all ~30).
        List<string>? columnFilter = null;
        if (payload.TryGetProperty("columns", out var colsEl)
            && colsEl.ValueKind == JsonValueKind.Array)
        {
            columnFilter = colsEl.EnumerateArray()
                .Where(e => e.ValueKind == JsonValueKind.String)
                .Select(e => e.GetString()!)
                .ToList();
        }

        // Optional: skip MIN/MAX/AVG/STDDEV — only compute COUNT (much faster).
        bool countOnly = payload.TryGetProperty("count_only", out var coEl)
            && coEl.ValueKind == JsonValueKind.True;

        try
        {
            var stats = await _repo.GetColumnStatsAsync(table, columnFilter, countOnly, ct);
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

    private async Task<object> HandleSeriesAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        var column = TryGetString(payload, "column");
        if (string.IsNullOrWhiteSpace(table)) return new { error = "missing field: table" };
        if (string.IsNullOrWhiteSpace(column)) return new { error = "missing field: column" };

        var maxPoints = (int)(TryGetInt64(payload, "max_points") ?? 600L);
        var startMs = TryGetInt64(payload, "start_ms");
        var endMs = TryGetInt64(payload, "end_ms");

        try
        {
            var series = await _repo.FetchSeriesAsync(
                table,
                column,
                maxPoints,
                startMs,
                endMs,
                ct);

            return new
            {
                table,
                column,
                max_points = maxPoints,
                source_rows = series.SourceRows,
                start_ms = series.StartMs,
                end_ms = series.EndMs,
                downsampled = series.SourceRows > series.Points.Count,
                points = series.Points,
            };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "series failed for {Table}.{Column}", table, column);
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

        // Skip the expensive COUNT(*) on pages beyond the first: the caller
        // already has the exact total from page 0. Fall back to the fast
        // pg_class.reltuples estimate instead. The caller can override this
        // by passing "include_total": true (force exact) or "include_total": false
        // (always use approximate, even on page 0).
        bool includeExactTotal;
        if (payload.TryGetProperty("include_total", out var itEl) && itEl.ValueKind == JsonValueKind.True)
            includeExactTotal = true;
        else if (payload.TryGetProperty("include_total", out itEl) && itEl.ValueKind == JsonValueKind.False)
            includeExactTotal = false;
        else
            includeExactTotal = page == 0;  // default: exact on first page, approx on subsequent

        try
        {
            var (exactTotal, estimateTotal, rows) =
                await _repo.BrowseRowsAsync(table, page, pageSize, orderDesc, includeExactTotal, ct);
            // Contract:
            //  total_rows           — exact COUNT(*), source of truth (only when computed)
            //  total_rows_estimate  — pg_class.reltuples (informational only)
            //  total_rows_known     — true iff total_rows is exact
            // Caller should pin total_rows on first page and IGNORE estimate
            // for pagination math (button availability, total page count).
            return new
            {
                table,
                page,
                page_size           = pageSize,
                total_rows          = exactTotal,
                total_rows_estimate = estimateTotal,
                total_rows_known    = exactTotal.HasValue,
                rows,
            };
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
        var updateFromMs = TryGetInt64(payload, "update_from_ms");

        try
        {
            var rowsUpdated = updateFromMs is long updateFrom
                ? await _repo.ComputeAndUpdateFeaturesSinceAsync(table, updateFrom, ct)
                : await _repo.ComputeAndUpdateFeaturesAsync(table, ct);
            return new
            {
                status = "ok",
                table,
                rows_updated = rowsUpdated,
                update_from_ms = updateFromMs,
            };
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
}
