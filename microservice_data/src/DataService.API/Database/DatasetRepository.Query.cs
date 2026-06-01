using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using DataService.API.Dataset;
using Npgsql;
using NpgsqlTypes;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
    // ── Rows ──────────────────────────────────────────────────────────────

    public async Task<IReadOnlyList<IReadOnlyDictionary<string, object?>>> FetchRowsAsync(
        string tableName,
        long startMs,
        long endMs,
        int? limit = null,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var limited = limit.GetValueOrDefault();
        if (limited < 0) limited = 0;

        // When `columns` is provided, project only those columns (plus the
        // synthetic timestamp_ms required at the Kafka boundary). Reduces
        // payload size and serialization time for chart-style requests that
        // only need OHLCV — full per-row data (raw 8 cols + 27+ feature cols
        // via SELECT *) is overkill for candlesticks and was the dominant
        // cost behind the rows-timeout 503 on cold tables.
        string projection;
        if (columns is { Count: > 0 })
        {
            var safeColumns = columns
                .Select(c => c?.Trim() ?? string.Empty)
                .Where(c => c.Length > 0)
                .Select(Safe)
                .Distinct()
                .ToList();

            if (safeColumns.Count > 0)
            {
                // timestamp_utc is always projected so MapRow can produce it,
                // even if the caller did not include it explicitly.
                if (!safeColumns.Contains("timestamp_utc"))
                    safeColumns.Insert(0, "timestamp_utc");

                projection = string.Join(", ", safeColumns.Select(c => $"\"{c}\""));
            }
            else
            {
                projection = "*";
            }
        }
        else
        {
            // SELECT * включает raw (8 колонок) и все feature-колонки (27).
            projection = "*";
        }

        var rows = await conn.QueryAsync(new CommandDefinition(
            $@"SELECT {projection},
                 (EXTRACT(EPOCH FROM timestamp_utc) * 1000)::bigint AS timestamp_ms
               FROM ""{tbl}""
               WHERE timestamp_utc >= @s AND timestamp_utc <= @e
               ORDER BY timestamp_utc
               LIMIT CASE WHEN @limit > 0 THEN @limit ELSE 2147483647 END",
            new { s = ToUtc(startMs), e = ToUtc(endMs), limit = limited }, cancellationToken: ct));
        return rows.Select(MapRow).ToList();
    }

    /// <summary>
    /// Returns the latest fixed-width window anchored at the newest timestamp
    /// in the table. This avoids global COUNT/MIN/MAX scans for chart requests.
    /// </summary>
    public async Task<IReadOnlyList<IReadOnlyDictionary<string, object?>>> FetchLatestWindowRowsAsync(
        string tableName,
        long stepMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct))
            return [];

        if (stepMs <= 0)
            throw new ArgumentOutOfRangeException(nameof(stepMs), "stepMs must be positive");

        if (limit < 1)
            return [];

        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);

        var newest = await conn.QueryFirstOrDefaultAsync<DateTime?>(
            new CommandDefinition(
                $@"SELECT timestamp_utc
                   FROM ""{tbl}""
                   ORDER BY timestamp_utc DESC
                   LIMIT 1",
                cancellationToken: ct));

        if (newest is null)
            return [];

        var endMs = ToMs(newest.Value);
        var startMs = endMs - (long)(limit - 1) * stepMs;
        return await FetchRowsAsync(tableName, startMs, endMs, limit, columns, ct);
    }

    /// <summary>
    /// Returns a compact time series for a numeric column. When the table has
    /// more rows than <paramref name="maxPoints"/>, values are aggregated into
    /// time buckets so the caller gets a stable chart payload instead of raw rows.
    /// </summary>
    public async Task<(
        long SourceRows,
        long? StartMs,
        long? EndMs,
        IReadOnlyList<IReadOnlyDictionary<string, object?>> Points)> FetchSeriesAsync(
            string tableName,
            string columnName,
            int maxPoints,
            long? startMs = null,
            long? endMs = null,
            CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct))
            return (0L, null, null, []);

        var tbl = Safe(tableName);
        var col = Safe(columnName);
        if (maxPoints < 1) maxPoints = 1;
        if (maxPoints > 4000) maxPoints = 4000;

        await using var conn = await _pg.OpenAsync(ct);

        var rows = await conn.QueryAsync(
            new CommandDefinition(
                $@"WITH filtered AS (
                                                SELECT timestamp_utc, ""{col}""::double precision AS value
                                                FROM ""{tbl}""
                                                WHERE ""{col}"" IS NOT NULL
                          AND (@start_utc IS NULL OR timestamp_utc >= @start_utc)
                          AND (@end_utc   IS NULL OR timestamp_utc <= @end_utc)
                    ),
                    bounds AS (
                        SELECT
                            MIN(timestamp_utc) AS min_ts,
                            MAX(timestamp_utc) AS max_ts,
                            COUNT(*)::bigint   AS row_count
                        FROM filtered
                    ),
                    bucketed AS (
                        SELECT
                            CASE
                                WHEN b.min_ts IS NULL OR b.max_ts IS NULL OR b.max_ts = b.min_ts THEN 0
                                ELSE LEAST(
                                    @bucket_count - 1,
                                    FLOOR(
                                        (EXTRACT(EPOCH FROM (f.timestamp_utc - b.min_ts)) * 1000.0)
                                        / NULLIF(EXTRACT(EPOCH FROM (b.max_ts - b.min_ts)) * 1000.0, 0)
                                        * @bucket_count
                                    )::int
                                )
                            END AS bucket,
                            f.timestamp_utc,
                            f.value,
                            b.row_count,
                            b.min_ts,
                            b.max_ts
                        FROM filtered f
                        CROSS JOIN bounds b
                    ),
                    aggregated AS (
                        SELECT
                            bucket,
                            MAX(timestamp_utc) AS timestamp_utc,
                            AVG(value)         AS avg_value,
                            MIN(value)         AS min_value,
                            MAX(value)         AS max_value,
                            COUNT(*)::int      AS sample_count,
                            MAX(row_count)     AS source_rows,
                            MAX(min_ts)        AS min_ts,
                            MAX(max_ts)        AS max_ts
                        FROM bucketed
                        GROUP BY bucket
                    )
                    SELECT
                        (EXTRACT(EPOCH FROM timestamp_utc) * 1000)::bigint AS timestamp_ms,
                        avg_value,
                        min_value,
                        max_value,
                        sample_count,
                        COALESCE(source_rows, 0)::bigint AS source_rows,
                        CASE WHEN min_ts IS NULL THEN NULL ELSE (EXTRACT(EPOCH FROM min_ts) * 1000)::bigint END AS series_start_ms,
                        CASE WHEN max_ts IS NULL THEN NULL ELSE (EXTRACT(EPOCH FROM max_ts) * 1000)::bigint END AS series_end_ms
                    FROM aggregated
                    ORDER BY bucket",
                new
                {
                    start_utc = startMs is long s ? (DateTime?)ToUtc(s) : null,
                    end_utc = endMs is long e ? (DateTime?)ToUtc(e) : null,
                    bucket_count = maxPoints,
                },
                cancellationToken: ct));

        long sourceRows = 0;
        long? seriesStartMs = null;
        long? seriesEndMs = null;
        var points = new List<IReadOnlyDictionary<string, object?>>();

        foreach (IDictionary<string, object?> raw in rows)
        {
            sourceRows = Math.Max(sourceRows, ToLong(raw.TryGetValue("source_rows", out var sr) ? sr : null));

            var start = ToNullableLong(raw.TryGetValue("series_start_ms", out var ss) ? ss : null);
            var end = ToNullableLong(raw.TryGetValue("series_end_ms", out var se) ? se : null);
            if (start is not null)
                seriesStartMs = seriesStartMs is null ? start : Math.Min(seriesStartMs.Value, start.Value);
            if (end is not null)
                seriesEndMs = seriesEndMs is null ? end : Math.Max(seriesEndMs.Value, end.Value);

            points.Add(new Dictionary<string, object?>
            {
                ["timestamp_ms"] = ToLong(raw.TryGetValue("timestamp_ms", out var ts) ? ts : null),
                ["value"] = raw.TryGetValue("avg_value", out var avg) ? avg : null,
                ["min"] = raw.TryGetValue("min_value", out var min) ? min : null,
                ["max"] = raw.TryGetValue("max_value", out var max) ? max : null,
                ["count"] = ToLong(raw.TryGetValue("sample_count", out var count) ? count : null),
            }.AsReadOnly());
        }

        return (sourceRows, seriesStartMs, seriesEndMs, points);
    }

    private static IReadOnlyDictionary<string, object?> MapRow(dynamic row)
    {
        var dict = (IDictionary<string, object?>)row;
        dict.Remove("timestamp_utc");
        return dict.AsReadOnly();
    }

    private static long ToLong(object? value) => value switch
    {
        long v => v,
        int v => v,
        short v => v,
        byte v => v,
        decimal v => (long)v,
        double v => (long)v,
        float v => (long)v,
        string s when long.TryParse(s, out var parsed) => parsed,
        _ => 0L,
    };

    private static long? ToNullableLong(object? value) => value switch
    {
        null => null,
        DBNull => null,
        _ => ToLong(value),
    };

    // ── CSV export (streaming) ────────────────────────────────────────────

    /// <summary>
    /// Stream a CSV export of the requested [startMs, endMs] window directly
    /// from PostgreSQL into <paramref name="output"/>. Uses
    /// <c>COPY (SELECT …) TO STDOUT WITH CSV HEADER</c>, which makes PostgreSQL
    /// generate the CSV on the server side and hand it to Npgsql row-by-row
    /// through a <see cref="TextReader"/> — no materialisation of the full
    /// result set, no <see cref="StringBuilder"/>, no intermediate
    /// <c>byte[]</c>. The <c>timestamp_utc</c> column is replaced by a computed
    /// <c>timestamp_ms</c> (epoch milliseconds) to match the Kafka contract
    /// used by other handlers. All other columns are selected via <c>t.*</c>
    /// minus <c>timestamp_utc</c>.
    ///
    /// The method takes ownership of nothing — <paramref name="output"/> is
    /// neither flushed nor closed here; the caller decides its lifetime.
    /// </summary>
    public async Task ExportCsvToStreamAsync(
        string tableName, long startMs, long endMs,
        Stream output, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);

        await using var conn = await _pg.OpenAsync(ct);

        // Discover the column list once so we can build a SELECT that replaces
        // timestamp_utc with a computed timestamp_ms while preserving order.
        var cols = (await conn.QueryAsync<string>(new CommandDefinition(
            @"SELECT column_name
              FROM information_schema.columns
              WHERE table_schema = 'public' AND table_name = @tbl
              ORDER BY ordinal_position",
            new { tbl }, cancellationToken: ct))).ToList();

        if (cols.Count == 0)
        {
            // Empty schema → emit a one-line placeholder so clients see *something*.
            var bytes = "no data\n"u8.ToArray();
            await output.WriteAsync(bytes, ct);
            return;
        }

        var projection = string.Join(", ", cols.Select(c =>
            c == "timestamp_utc"
                ? "(EXTRACT(EPOCH FROM timestamp_utc) * 1000)::bigint AS timestamp_ms"
                : $"\"{Safe(c)}\""));

        // Format as ISO-8601 strings so downstream readers don't need epoch math.
        // NOTE: start/end are inlined as literal timestamptz — COPY does not
        // accept bound parameters, and we've already validated them as 64-bit
        // integers at the Kafka boundary.
        var startIso = DateTimeOffset.FromUnixTimeMilliseconds(startMs)
            .UtcDateTime.ToString("O");
        var endIso = DateTimeOffset.FromUnixTimeMilliseconds(endMs)
            .UtcDateTime.ToString("O");

        var sql =
            $"COPY (SELECT {projection} FROM \"{tbl}\" " +
            $"WHERE timestamp_utc >= '{startIso}'::timestamptz " +
            $"AND timestamp_utc <= '{endIso}'::timestamptz " +
            $"ORDER BY timestamp_utc) TO STDOUT WITH CSV HEADER";

        // BeginTextExport returns a TextReader that pulls rows from the
        // backend as they arrive — no server-side materialisation, no
        // client-side buffering of the whole result.
        using var reader = await conn.BeginTextExportAsync(sql, ct);

        // Transcode TextReader (UTF-16 chars) → UTF-8 bytes with a small
        // buffer. StreamWriter keeps the encoder state across chunks so
        // multibyte characters never get split.
        await using var writer = new StreamWriter(
            output,
            new UTF8Encoding(encoderShouldEmitUTF8Identifier: false),
            bufferSize: 64 * 1024,
            leaveOpen: true);

        const int ChunkChars = 32 * 1024; // ~32K chars ≈ ~32–96 KB UTF-8
        var buffer = new char[ChunkChars];
        int read;
        while ((read = await reader.ReadAsync(buffer.AsMemory(0, ChunkChars), ct)) > 0)
        {
            await writer.WriteAsync(buffer.AsMemory(0, read), ct);
        }
        await writer.FlushAsync();
    }

    // ── Column statistics (Anomaly → Inspect) ─────────────────────────────

    public sealed record ColumnInfo(string Name, string DataType);

    public sealed record ColumnStat(
        string Name,
        string Dtype,
        long NonNull,
        double? Min,
        double? Max,
        double? Mean,
        double? Std);

    public sealed record ColumnStatsResult(long TotalRows, IReadOnlyList<ColumnStat> Columns);

    private static readonly HashSet<string> _numericTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        "numeric", "double precision", "real",
        "integer", "bigint", "smallint",
    };

    /// <summary>
    /// Per-column non-null counts plus min/max/mean/std for numeric columns.
    /// One dynamic aggregation query — O(N) scan of the table.
    /// </summary>
    /// <param name="tableName">Target table.</param>
    /// <param name="columnFilter">
    /// When non-null, only the columns in this list are queried.
    /// Columns that do not exist in the table schema are silently skipped.
    /// </param>
    /// <param name="countOnly">
    /// When <see langword="true"/>, only <c>COUNT(*)</c> and per-column
    /// <c>COUNT(col)</c> are computed (no MIN/MAX/AVG/STDDEV).  All numeric
    /// stat fields in the returned <see cref="ColumnStat"/> records will be
    /// <see langword="null"/>.  Much faster for quality-audit use-cases that
    /// only need fill ratios.
    /// </param>
    public async Task<ColumnStatsResult?> GetColumnStatsAsync(
        string tableName,
        IReadOnlyList<string>? columnFilter = null,
        bool countOnly = false,
        CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return null;
        var tbl = Safe(tableName);

        await using var conn = await _pg.OpenAsync(ct);

        var allCols = (await conn.QueryAsync<(string column_name, string data_type)>(
            new CommandDefinition(
                @"SELECT column_name, data_type
                  FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = @tbl
                  ORDER BY ordinal_position",
                new { tbl }, cancellationToken: ct)))
            .Select(r => new ColumnInfo(r.column_name, r.data_type))
            .ToList();

        // Apply column filter (if provided) while preserving schema order.
        var cols = columnFilter is { Count: > 0 }
            ? allCols.Where(c => columnFilter.Contains(c.Name, StringComparer.OrdinalIgnoreCase)).ToList()
            : allCols;

        if (cols.Count == 0) return new ColumnStatsResult(0, Array.Empty<ColumnStat>());

        // Build one big SELECT with aggregations for every column.
        var sb = new StringBuilder();
        sb.Append("SELECT COUNT(*)::bigint AS total_rows");
        foreach (var c in cols)
        {
            var safeCol = Safe(c.Name);
            sb.Append($@", COUNT(""{safeCol}"")::bigint AS ""nn_{safeCol}""");
            if (!countOnly && _numericTypes.Contains(c.DataType))
            {
                sb.Append($@", MIN(""{safeCol}"")::float8 AS ""min_{safeCol}""");
                sb.Append($@", MAX(""{safeCol}"")::float8 AS ""max_{safeCol}""");
                sb.Append($@", AVG(""{safeCol}"")::float8 AS ""avg_{safeCol}""");
                sb.Append($@", STDDEV_POP(""{safeCol}"")::float8 AS ""std_{safeCol}""");
            }
        }
        sb.Append($@" FROM ""{tbl}""");

        var row = await conn.QuerySingleAsync<dynamic>(
            new CommandDefinition(sb.ToString(), cancellationToken: ct));
        var dict = (IDictionary<string, object?>)row;

        long totalRows = Convert.ToInt64(dict["total_rows"] ?? 0L);

        var stats = new List<ColumnStat>(cols.Count);
        foreach (var c in cols)
        {
            var safeCol = Safe(c.Name);
            long nn = dict.TryGetValue($"nn_{safeCol}", out var nnv) && nnv is not null
                ? Convert.ToInt64(nnv) : 0L;
            double? min = null, max = null, mean = null, std = null;
            if (!countOnly && _numericTypes.Contains(c.DataType))
            {
                min  = ToDbl(dict, $"min_{safeCol}");
                max  = ToDbl(dict, $"max_{safeCol}");
                mean = ToDbl(dict, $"avg_{safeCol}");
                std  = ToDbl(dict, $"std_{safeCol}");
            }
            stats.Add(new ColumnStat(c.Name, c.DataType, nn, min, max, mean, std));
        }

        return new ColumnStatsResult(totalRows, stats);
    }

    private static double? ToDbl(IDictionary<string, object?> d, string key) =>
        d.TryGetValue(key, out var v) && v is not null ? Convert.ToDouble(v) : null;

    // ── Column histogram (Anomaly → Inspect) ──────────────────────────────

    public sealed record HistogramBucket(double RangeStart, double RangeEnd, long Count);

    public sealed record HistogramResult(
        string Column,
        double? Min,
        double? Max,
        IReadOnlyList<HistogramBucket> Buckets);

    /// <summary>
    /// Equal-width histogram over the non-null values of a numeric column
    /// using PostgreSQL's <c>width_bucket</c>.
    /// </summary>
    public async Task<HistogramResult?> GetColumnHistogramAsync(
        string tableName, string columnName, int buckets, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return null;
        var tbl = Safe(tableName);
        var col = Safe(columnName);
        if (buckets < 2)  buckets = 2;
        if (buckets > 500) buckets = 500;

        await using var conn = await _pg.OpenAsync(ct);

        // Bounds first — null result if no non-null values.
        var bounds = await conn.QuerySingleOrDefaultAsync<(double? lo, double? hi)>(
            new CommandDefinition(
                $@"SELECT MIN(""{col}"")::double precision AS lo,
                          MAX(""{col}"")::double precision AS hi
                   FROM ""{tbl}"" WHERE ""{col}"" IS NOT NULL",
                cancellationToken: ct));
        if (bounds.lo is null || bounds.hi is null)
            return new HistogramResult(columnName, null, null, Array.Empty<HistogramBucket>());

        double lo = bounds.lo.Value;
        double hi = bounds.hi.Value;
        // Degenerate case — single value: one bucket of width 0.
        if (hi <= lo)
            return new HistogramResult(columnName, lo, hi,
                new[] { new HistogramBucket(lo, hi, await conn.ExecuteScalarAsync<long>(
                    new CommandDefinition(
                        $@"SELECT COUNT(*)::bigint FROM ""{tbl}"" WHERE ""{col}"" IS NOT NULL",
                        cancellationToken: ct))) });

        // width_bucket uses an exclusive upper bound; nudge by ulp so max lands
        // in bucket @n, not @n+1.
        double upperExcl = hi + (hi - lo) * 1e-9;

        var rows = await conn.QueryAsync<(int bkt, long cnt)>(new CommandDefinition(
            $@"SELECT width_bucket(""{col}""::double precision, @lo, @upper, @n) AS bkt,
                      COUNT(*)::bigint AS cnt
               FROM ""{tbl}""
               WHERE ""{col}"" IS NOT NULL
               GROUP BY bkt
               ORDER BY bkt",
            new { lo, upper = upperExcl, n = buckets }, cancellationToken: ct));

        var counts = new long[buckets];
        foreach (var r in rows)
        {
            // width_bucket returns 1..N in-range; out-of-range becomes 0 or N+1.
            var idx = r.bkt - 1;
            if (idx < 0) idx = 0;
            if (idx >= buckets) idx = buckets - 1;
            counts[idx] += r.cnt;
        }

        double step = (hi - lo) / buckets;
        var result = new List<HistogramBucket>(buckets);
        for (int i = 0; i < buckets; i++)
            result.Add(new HistogramBucket(lo + step * i, lo + step * (i + 1), counts[i]));

        return new HistogramResult(columnName, lo, hi, result);
    }

    // ── Browse rows (paginated) ────────────────────────────────────────────

    /// <summary>
    /// Returns a page of raw rows from the table together with both the exact and
    /// estimated total row counts. Exact is the result of <c>COUNT(*)</c> and is
    /// authoritative; estimate is read from <c>pg_class.reltuples</c> (O(1), may
    /// lag a few percent). Either may be <c>null</c> depending on
    /// <paramref name="includeExactTotal"/>:
    /// <list type="bullet">
    /// <item>Exact pre-empts estimate when requested — caller pins this and
    ///   keeps it as the source of truth across subsequent pages.</item>
    /// <item>When the caller skips exact (e.g. page > 0), only estimate is
    ///   returned; caller treats it as informational, not page-state truth.</item>
    /// </list>
    /// DateTime/DateTimeOffset values are serialised as ISO-8601 strings.
    /// Decimal values are serialised via .ToString() to avoid System.Decimal overflow.
    /// </summary>
    public async Task<(long? ExactTotal, long? EstimateTotal, IReadOnlyList<IDictionary<string, object?>> Rows)>
        BrowseRowsAsync(
            string tableName,
            int    page,
            int    pageSize,
            bool   orderDesc,
            bool   includeExactTotal = true,
            CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct))
            return (0L, 0L, Array.Empty<IDictionary<string, object?>>());

        var tbl = Safe(tableName);
        if (pageSize < 1)   pageSize = 1;
        if (pageSize > 500) pageSize = 500;

        await using var conn = await _pg.OpenAsync(ct);

        long? exactTotal = null;
        long? estimateTotal = null;
        if (includeExactTotal)
        {
            exactTotal = await conn.ExecuteScalarAsync<long>(
                new CommandDefinition(
                    $@"SELECT COUNT(*)::bigint FROM ""{tbl}""",
                    cancellationToken: ct));
        }
        else
        {
            // reltuples is updated by ANALYZE / autovacuum and is O(1).
            // It may lag by a few percent for recently modified tables, which
            // is acceptable when the caller already displayed the exact total
            // on the first page and pins it as page-state source of truth.
            estimateTotal = await conn.ExecuteScalarAsync<long>(
                new CommandDefinition(
                    @"SELECT GREATEST(reltuples::bigint, 0)
                        FROM pg_class
                       WHERE relname = @name",
                    new { name = tableName },
                    cancellationToken: ct));
        }

        var dir    = orderDesc ? "DESC" : "ASC";
        var dynRows = await conn.QueryAsync<dynamic>(
            new CommandDefinition(
                $@"SELECT * FROM ""{tbl}""
                   ORDER BY timestamp_utc {dir}
                   LIMIT @limit OFFSET @offset",
                new { limit = pageSize, offset = (long)page * pageSize },
                cancellationToken: ct));

        var result = new List<IDictionary<string, object?>>();
        foreach (IDictionary<string, object?> rawRow in dynRows)
        {
            var mapped = new Dictionary<string, object?>(rawRow.Count);
            foreach (var kv in rawRow)
            {
                mapped[kv.Key] = kv.Value switch
                {
                    DateTime dt         => dt.ToString("o"),
                    DateTimeOffset dto  => dto.ToString("o"),
                    decimal d           => d.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    var v               => v,
                };
            }
            result.Add(mapped);
        }
        return (exactTotal, estimateTotal, result);
    }
}
