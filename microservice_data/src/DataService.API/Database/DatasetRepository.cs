using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using DataService.API.Dataset;
using Npgsql;
using NpgsqlTypes;

namespace DataService.API.Database;

/// <summary>
/// All PostgreSQL dataset operations.
///
/// Schema (market data tables are named "{symbol}_{timeframe}", e.g. "btcusdt_5m"):
///   timestamp_utc   TIMESTAMP WITH TIME ZONE PRIMARY KEY
///   symbol          VARCHAR
///   exchange        VARCHAR
///   timeframe       VARCHAR
///   close_price     NUMERIC   (close price)
///   open_price      NUMERIC
///   high_price      NUMERIC
///   low_price       NUMERIC
///   volume          NUMERIC
///   turnover        NUMERIC
///   funding_rate    NUMERIC
///   open_interest   NUMERIC
///   rsi             NUMERIC
///   … + 37 feature columns (double precision, nullable)
///
/// All timestamps crossing the Kafka boundary are epoch milliseconds.
/// </summary>
public sealed partial class DatasetRepository
{
    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<DatasetRepository> _log;
    private const int NoTimeout = 0;

    // Only lower-case letters, digits, underscores are safe in identifiers.
    private static readonly Regex _safeIdentifier =
        new(@"^[a-z0-9_]+$", RegexOptions.Compiled);

    public DatasetRepository(PostgresConnectionFactory pg, ILogger<DatasetRepository> log)
    {
        _pg  = pg;
        _log = log;
    }

    // ── Identifier guard ──────────────────────────────────────────────────
    private static string Safe(string name)
    {
        var n = name.Trim().ToLowerInvariant();
        if (!_safeIdentifier.IsMatch(n))
            throw new ArgumentException($"Unsafe SQL identifier: '{name}'");
        return n;
    }

    private static DateTime ToUtc(long ms) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ms).UtcDateTime;

    private static long ToMs(DateTime utc) =>
        new DateTimeOffset(DateTime.SpecifyKind(utc, DateTimeKind.Utc)).ToUnixTimeMilliseconds();

    // ── Schema helpers ────────────────────────────────────────────────────

    public async Task<bool> TableExistsAsync(string tableName, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var result = await conn.ExecuteScalarAsync<object>(
            "SELECT to_regclass(@tbl)::text", new { tbl = $"public.{tbl}" });
        return result is not null and not DBNull;
    }

    public async Task<IReadOnlyList<Dictionary<string, object>>> ReadTableSchemaAsync(
        string tableName, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<(string column, string data_type)>(new CommandDefinition(
            @"SELECT column_name AS column, data_type
              FROM information_schema.columns
              WHERE table_schema = 'public' AND table_name = @tbl
              ORDER BY ordinal_position",
            new { tbl }, cancellationToken: ct));
        return rows.Select(r => new Dictionary<string, object>
        {
            ["column"]    = r.column,
            ["data_type"] = r.data_type,
        }).ToList();
    }

    public async Task<IReadOnlyList<string>> ListTablesAsync(CancellationToken ct = default)
    {
        await using var conn = await _pg.OpenAsync(ct);
        var tables = await conn.QueryAsync<string>(new CommandDefinition(
            @"SELECT table_name FROM information_schema.tables
              WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
              ORDER BY table_name", cancellationToken: ct));
        return tables.ToList();
    }

    /// <summary>
    /// Create the market-data table with the canonical raw schema plus
    /// the feature columns (all nullable <c>double precision</c>) if it
    /// does not yet exist. Idempotent. Column list is driven by
    /// <see cref="DatasetConstants.RawTableSchema"/> and
    /// <see cref="DatasetConstants.FeatureTableSchema"/>.
    /// </summary>
    public async Task CreateTableIfNotExistsAsync(string tableName, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);

        // Build column definitions from the canonical schema constants.
        // The first raw column (timestamp_utc) becomes the PRIMARY KEY.
        var rawCols = DatasetConstants.RawTableSchema
            .Select((f, i) => i == 0
                ? $"\"timestamp_utc\" TIMESTAMP WITH TIME ZONE PRIMARY KEY"
                : $"\"{f.Column}\" {f.SqlType}");
        var featureCols = DatasetConstants.FeatureTableSchema
            .Select(f => $"\"{f.Column}\" {f.SqlType}");
        var allCols = string.Join(",\n                ", rawCols.Concat(featureCols));

        var sql = $@"
            CREATE TABLE IF NOT EXISTS ""{tbl}"" (
                {allCols}
            );";
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, cancellationToken: ct));

        // Schema migration: legacy tables created before the OHLCV /
        // turnover / feature columns were introduced need them back-filled.
        // Each ALTER is idempotent (ADD COLUMN IF NOT EXISTS), so this is
        // safe to run on every ingest call.
        //
        // Phase-4 candle-source-of-truth migration: the historical
        // `index_price` column was always populated from the Bybit kline
        // close — its name was misleading. Rename it to `close_price` so
        // the OHLC tuple stored on disk is unambiguous. The rename runs
        // before the ADD COLUMN sweep so the new schema lands cleanly.
        var renameSql = $@"
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name   = '{tbl}'
                      AND column_name  = 'index_price'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name   = '{tbl}'
                      AND column_name  = 'close_price'
                ) THEN
                    EXECUTE 'ALTER TABLE ""{tbl}"" RENAME COLUMN index_price TO close_price';
                END IF;
            END$$;";
        await conn.ExecuteAsync(new CommandDefinition(renameSql, cancellationToken: ct));

        var alters = new StringBuilder();
        foreach (var f in DatasetConstants.RawTableSchema)
        {
            if (string.Equals(f.Column, "timestamp_utc", StringComparison.OrdinalIgnoreCase))
                continue;
            alters.AppendLine(
                $"ALTER TABLE \"{tbl}\" ADD COLUMN IF NOT EXISTS \"{f.Column}\" {f.SqlType};");
        }
        foreach (var f in DatasetConstants.FeatureTableSchema)
        {
            alters.AppendLine(
                $"ALTER TABLE \"{tbl}\" ADD COLUMN IF NOT EXISTS \"{f.Column}\" {f.SqlType};");
        }
        if (alters.Length > 0)
            await conn.ExecuteAsync(new CommandDefinition(alters.ToString(), cancellationToken: ct));
    }

    // ── Coverage ──────────────────────────────────────────────────────────

    /// <summary>
    /// Returns (rows, minTsMs, maxTsMs) or null when table is empty / missing.
    /// </summary>
    public async Task<(long Rows, long MinTsMs, long MaxTsMs)?> GetCoverageAsync(
        string tableName, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var row = await conn.QuerySingleAsync<(long count, DateTime? min, DateTime? max)>(
            new CommandDefinition(
                $@"SELECT COUNT(*)::bigint,
                          MIN(timestamp_utc) AT TIME ZONE 'UTC',
                          MAX(timestamp_utc) AT TIME ZONE 'UTC'
                   FROM ""{tbl}""",
                cancellationToken: ct));
        if (row.count == 0 || row.min is null) return null;
        return (row.count, ToMs(row.min.Value), ToMs(row.max!.Value));
    }

    public async Task<(long Rows, long MinTsMs, long MaxTsMs)?> GetCoverageIfExistsAsync(
        string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return null;
        return await GetCoverageAsync(tableName, ct);
    }

    /// <summary>
    /// Returns only the observed [minTsMs, maxTsMs] bounds for a table.
    /// Uses index-friendly ORDER BY ... LIMIT 1 probes instead of a full COUNT(*).
    /// Returns null when the table is missing or empty.
    /// </summary>
    public async Task<(long MinTsMs, long MaxTsMs)?> GetBoundsIfExistsAsync(
        string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return null;

        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);

        var min = await conn.QueryFirstOrDefaultAsync<DateTime?>(
            new CommandDefinition(
                $@"SELECT timestamp_utc AT TIME ZONE 'UTC'
                   FROM ""{tbl}""
                   ORDER BY timestamp_utc ASC
                   LIMIT 1",
                cancellationToken: ct));

        if (min is null)
            return null;

        var max = await conn.QueryFirstAsync<DateTime>(
            new CommandDefinition(
                $@"SELECT timestamp_utc AT TIME ZONE 'UTC'
                   FROM ""{tbl}""
                   ORDER BY timestamp_utc DESC
                   LIMIT 1",
                cancellationToken: ct));

        return (ToMs(min.Value), ToMs(max));
    }

    /// <summary>
    /// Coverage scoped to an explicit [startMs, endMs] window. Returns
    /// <c>(rowsInRange, expectedInRange, gaps)</c>; expected is computed via
    /// <c>generate_series</c> with the supplied step. Returns <c>null</c> if
    /// the table doesn't exist.
    /// </summary>
    public async Task<(long RowsInRange, long ExpectedInRange, long Gaps)?> GetCoverageRangeAsync(
        string tableName, long startMs, long endMs, long stepMs, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return null;
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var row = await conn.QueryFirstAsync<(long rows_in_range, long expected_in_range)>(
            new CommandDefinition(
                $@"SELECT
                    (SELECT COUNT(*) FROM ""{tbl}""
                       WHERE timestamp_utc >= @s AND timestamp_utc <= @e) AS rows_in_range,
                    (SELECT COUNT(*) FROM generate_series(
                        @s::timestamptz, @e::timestamptz,
                        make_interval(secs => @step_s))) AS expected_in_range",
                new { s = ToUtc(startMs), e = ToUtc(endMs), step_s = stepMs / 1000.0 },
                cancellationToken: ct));
        var gaps = row.expected_in_range > row.rows_in_range
            ? row.expected_in_range - row.rows_in_range
            : 0L;
        return (row.rows_in_range, row.expected_in_range, gaps);
    }

    // ── Timestamps ────────────────────────────────────────────────────────

    public async Task<IReadOnlyList<long>> FetchTimestampsAsync(
        string tableName, long startMs, long endMs, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<DateTime>(new CommandDefinition(
            $@"SELECT timestamp_utc FROM ""{tbl}""
               WHERE timestamp_utc >= @s AND timestamp_utc <= @e
               ORDER BY timestamp_utc",
            new { s = ToUtc(startMs), e = ToUtc(endMs) }, cancellationToken: ct));
        return rows.Select(ToMs).ToList();
    }

    /// <summary>Find missing timestamps in [startMs, endMs] using generate_series.</summary>
    public async Task<IReadOnlyList<long>> FindMissingTimestampsAsync(
        string tableName, long startMs, long endMs, long stepMs, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<DateTime>(new CommandDefinition(
            $@"SELECT g.ts
               FROM generate_series(@s::timestamptz, @e::timestamptz,
                                    make_interval(secs => @step_s)) AS g(ts)
               WHERE NOT EXISTS (
                   SELECT 1 FROM ""{tbl}"" t WHERE t.timestamp_utc = g.ts
               )
               ORDER BY g.ts",
            new { s = ToUtc(startMs), e = ToUtc(endMs), step_s = stepMs / 1000.0 },
            cancellationToken: ct));
        return rows.Select(ToMs).ToList();
    }

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

    // ── Bulk upsert ───────────────────────────────────────────────────────

    public sealed record MarketRow(
        long   TimestampMs,
        string Symbol,
        string Exchange,
        string Timeframe,
        decimal? OpenPrice,
        decimal? HighPrice,
        decimal? LowPrice,
        decimal? ClosePrice,
        decimal? Volume,
        decimal? Turnover,
        decimal? FundingRate,
        decimal? OpenInterest,
        decimal? Rsi);

    /// <summary>
    /// Upsert rows by primary key <c>timestamp_utc</c>. Uses unnested array
    /// parameters for efficiency; internally batched by
    /// <see cref="DataService.API.Dataset.DatasetConstants.UpsertBatchSize"/>.
    /// Returns the total number of rows written.
    /// </summary>
    public async Task<long> BulkUpsertAsync(
        string tableName,
        IReadOnlyList<MarketRow> rows,
        CancellationToken ct = default)
    {
        if (rows.Count == 0) return 0;
        var tbl = Safe(tableName);
        var batchSize = DataService.API.Dataset.DatasetConstants.UpsertBatchSize;

        await using var conn = await _pg.OpenAsync(ct);
        long total = 0;

        for (int offset = 0; offset < rows.Count; offset += batchSize)
        {
            var slice = rows.Skip(offset).Take(batchSize).ToArray();
            var n = slice.Length;

            var ts       = new DateTime[n];
            var sym      = new string[n];
            var exch     = new string[n];
            var tf       = new string[n];
            var openP    = new decimal?[n];
            var highP    = new decimal?[n];
            var lowP     = new decimal?[n];
            var closeP   = new decimal?[n];
            var vol      = new decimal?[n];
            var tv       = new decimal?[n];
            var fund     = new decimal?[n];
            var oi       = new decimal?[n];
            var rsi      = new decimal?[n];

            for (int i = 0; i < n; i++)
            {
                var r = slice[i];
                ts[i]     = ToUtc(r.TimestampMs);
                sym[i]    = r.Symbol;
                exch[i]   = r.Exchange;
                tf[i]     = r.Timeframe;
                openP[i]  = r.OpenPrice;
                highP[i]  = r.HighPrice;
                lowP[i]   = r.LowPrice;
                closeP[i] = r.ClosePrice;
                vol[i]    = r.Volume;
                tv[i]     = r.Turnover;
                fund[i]   = r.FundingRate;
                oi[i]     = r.OpenInterest;
                rsi[i]    = r.Rsi;
            }

            var sql = $@"
                INSERT INTO ""{tbl}"" (
                    timestamp_utc, symbol, exchange, timeframe,
                    open_price, high_price, low_price, close_price,
                    volume, turnover,
                    funding_rate, open_interest, rsi
                )
                SELECT * FROM UNNEST (
                    @ts::timestamptz[],
                    @sym::varchar[],
                    @exch::varchar[],
                    @tf::varchar[],
                    @openP::numeric[],
                    @highP::numeric[],
                    @lowP::numeric[],
                    @closeP::numeric[],
                    @vol::numeric[],
                    @tv::numeric[],
                    @fund::numeric[],
                    @oi::numeric[],
                    @rsi::numeric[]
                )
                ON CONFLICT (timestamp_utc) DO UPDATE SET
                    symbol        = EXCLUDED.symbol,
                    exchange      = EXCLUDED.exchange,
                    timeframe     = EXCLUDED.timeframe,
                    open_price    = EXCLUDED.open_price,
                    high_price    = EXCLUDED.high_price,
                    low_price     = EXCLUDED.low_price,
                    close_price   = EXCLUDED.close_price,
                    volume        = EXCLUDED.volume,
                    turnover      = EXCLUDED.turnover,
                    funding_rate  = EXCLUDED.funding_rate,
                    open_interest = EXCLUDED.open_interest,
                    rsi           = EXCLUDED.rsi;";

            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.Add(new NpgsqlParameter("ts",     NpgsqlDbType.Array | NpgsqlDbType.TimestampTz) { Value = ts });
            cmd.Parameters.Add(new NpgsqlParameter("sym",    NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = sym });
            cmd.Parameters.Add(new NpgsqlParameter("exch",   NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = exch });
            cmd.Parameters.Add(new NpgsqlParameter("tf",     NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = tf });
            cmd.Parameters.Add(new NpgsqlParameter("openP",  NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = openP });
            cmd.Parameters.Add(new NpgsqlParameter("highP",  NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = highP });
            cmd.Parameters.Add(new NpgsqlParameter("lowP",   NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = lowP });
            cmd.Parameters.Add(new NpgsqlParameter("closeP", NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = closeP });
            cmd.Parameters.Add(new NpgsqlParameter("vol",    NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = vol });
            cmd.Parameters.Add(new NpgsqlParameter("tv",     NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = tv });
            cmd.Parameters.Add(new NpgsqlParameter("fund",   NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = fund });
            cmd.Parameters.Add(new NpgsqlParameter("oi",     NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = oi });
            cmd.Parameters.Add(new NpgsqlParameter("rsi",    NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = rsi });

            total += await cmd.ExecuteNonQueryAsync(ct);
        }
        return total;
    }

    // ── OHLCV-only upsert ────────────────────────────────────────────────

    /// <summary>
    /// One row of OHLCV data to be merged into an existing market table.
    /// Used by <see cref="BulkUpdateOhlcvAsync"/>.
    ///
    /// All four price components (open/high/low/close) MUST come from the
    /// same exchange candle so the persisted OHLC tuple is internally
    /// consistent. The handler that constructs <see cref="OhlcvRow"/>
    /// instances is responsible for validating
    /// <c>low ≤ min(open, close) ≤ max(open, close) ≤ high</c> before
    /// queueing the row for upsert.
    /// </summary>
    public sealed record OhlcvRow(
        long     TimestampMs,
        decimal? Open,
        decimal? High,
        decimal? Low,
        decimal? Close,
        decimal? Volume,
        decimal? Turnover);

    /// <summary>
    /// One closed live candle produced by <c>MarketWatcherService</c>.
    ///
    /// The live watcher only has authoritative OHLC prices. Volume,
    /// turnover and derived columns must stay untouched on conflict.
    /// </summary>
    public sealed record LiveCandleRow(
        long    TimestampMs,
        decimal Open,
        decimal High,
        decimal Low,
        decimal Close);

    /// <summary>
    /// Insert/update the six raw OHLCV columns (open_price, high_price,
    /// low_price, close_price, volume, turnover) keyed by
    /// <c>timestamp_utc</c>. Non-OHLCV columns (funding_rate,
    /// open_interest, rsi, derived features) are preserved on conflict —
    /// the ON CONFLICT clause touches only OHLCV cells.
    ///
    /// Phase-4 candle-source-of-truth: <c>close_price</c> is always written
    /// alongside O/H/L so the persisted candle is a single tuple. Callers
    /// must reject rows where the four prices come from different sources.
    ///
    /// On a fresh row (no conflict) the row is inserted with the provided
    /// symbol/exchange/timeframe identity and OHLCV values; all other
    /// columns remain NULL until a separate operation populates them.
    ///
    /// Returns the total number of rows affected.
    /// </summary>
    public async Task<long> BulkUpdateOhlcvAsync(
        string tableName,
        string symbol,
        string exchange,
        string timeframe,
        IReadOnlyList<OhlcvRow> rows,
        CancellationToken ct = default)
    {
        if (rows.Count == 0) return 0;
        var tbl = Safe(tableName);
        var batchSize = DataService.API.Dataset.DatasetConstants.UpsertBatchSize;

        await using var conn = await _pg.OpenAsync(ct);
        long total = 0;

        for (int offset = 0; offset < rows.Count; offset += batchSize)
        {
            var slice = rows.Skip(offset).Take(batchSize).ToArray();
            var n = slice.Length;

            var ts     = new DateTime[n];
            var openP  = new decimal?[n];
            var highP  = new decimal?[n];
            var lowP   = new decimal?[n];
            var closeP = new decimal?[n];
            var vol    = new decimal?[n];
            var tv     = new decimal?[n];

            for (int i = 0; i < n; i++)
            {
                var r = slice[i];
                ts[i]     = ToUtc(r.TimestampMs);
                openP[i]  = r.Open;
                highP[i]  = r.High;
                lowP[i]   = r.Low;
                closeP[i] = r.Close;
                vol[i]    = r.Volume;
                tv[i]     = r.Turnover;
            }

            // INSERT identity + OHLCV; on conflict update ONLY OHLCV cells —
            // funding_rate, open_interest, rsi and all feature columns are
            // intentionally excluded so existing data is preserved.
            var sql = $@"
                INSERT INTO ""{tbl}"" (
                    timestamp_utc, symbol, exchange, timeframe,
                    open_price, high_price, low_price, close_price,
                    volume, turnover
                )
                SELECT * FROM UNNEST (
                    @ts::timestamptz[],
                    @sym::varchar[],
                    @exch::varchar[],
                    @tf::varchar[],
                    @openP::numeric[],
                    @highP::numeric[],
                    @lowP::numeric[],
                    @closeP::numeric[],
                    @vol::numeric[],
                    @tv::numeric[]
                )
                ON CONFLICT (timestamp_utc) DO UPDATE SET
                    open_price  = EXCLUDED.open_price,
                    high_price  = EXCLUDED.high_price,
                    low_price   = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price,
                    volume      = EXCLUDED.volume,
                    turnover    = EXCLUDED.turnover;";

            await using var cmd = new NpgsqlCommand(sql, conn);
            // symbol/exchange/timeframe arrays are constant-valued (one entry per row)
            // and only used during the INSERT path; ON CONFLICT preserves any
            // pre-existing identity values.
            var symArr  = Enumerable.Repeat(symbol,    n).ToArray();
            var exchArr = Enumerable.Repeat(exchange,  n).ToArray();
            var tfArr   = Enumerable.Repeat(timeframe, n).ToArray();
            cmd.Parameters.Add(new NpgsqlParameter("ts",     NpgsqlDbType.Array | NpgsqlDbType.TimestampTz) { Value = ts });
            cmd.Parameters.Add(new NpgsqlParameter("sym",    NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = symArr });
            cmd.Parameters.Add(new NpgsqlParameter("exch",   NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = exchArr });
            cmd.Parameters.Add(new NpgsqlParameter("tf",     NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = tfArr });
            cmd.Parameters.Add(new NpgsqlParameter("openP",  NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = openP });
            cmd.Parameters.Add(new NpgsqlParameter("highP",  NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = highP });
            cmd.Parameters.Add(new NpgsqlParameter("lowP",   NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = lowP });
            cmd.Parameters.Add(new NpgsqlParameter("closeP", NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = closeP });
            cmd.Parameters.Add(new NpgsqlParameter("vol",    NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = vol });
            cmd.Parameters.Add(new NpgsqlParameter("tv",     NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = tv });

            total += await cmd.ExecuteNonQueryAsync(ct);
        }
        return total;
    }

    /// <summary>
    /// Insert or update only OHLC prices for live closed candles. Existing
    /// volume, turnover and derived columns are preserved on conflict.
    /// </summary>
    public async Task<long> BulkUpsertLiveCandlesAsync(
        string tableName,
        string symbol,
        string exchange,
        string timeframe,
        IReadOnlyList<LiveCandleRow> rows,
        CancellationToken ct = default)
    {
        if (rows.Count == 0) return 0;
        var tbl = Safe(tableName);
        var batchSize = DataService.API.Dataset.DatasetConstants.UpsertBatchSize;

        await using var conn = await _pg.OpenAsync(ct);
        long total = 0;

        for (int offset = 0; offset < rows.Count; offset += batchSize)
        {
            var slice = rows.Skip(offset).Take(batchSize).ToArray();
            var n = slice.Length;

            var ts = new DateTime[n];
            var openP = new decimal[n];
            var highP = new decimal[n];
            var lowP = new decimal[n];
            var closeP = new decimal[n];

            for (int i = 0; i < n; i++)
            {
                var row = slice[i];
                ts[i] = ToUtc(row.TimestampMs);
                openP[i] = row.Open;
                highP[i] = row.High;
                lowP[i] = row.Low;
                closeP[i] = row.Close;
            }

            var sql = $@"
                INSERT INTO ""{tbl}"" (
                    timestamp_utc, symbol, exchange, timeframe,
                    open_price, high_price, low_price, close_price
                )
                SELECT
                    payload.ts,
                    @symbol,
                    @exchange,
                    @timeframe,
                    payload.open_price,
                    payload.high_price,
                    payload.low_price,
                    payload.close_price
                FROM UNNEST(
                    @ts::timestamptz[],
                    @openP::numeric[],
                    @highP::numeric[],
                    @lowP::numeric[],
                    @closeP::numeric[]
                ) AS payload(ts, open_price, high_price, low_price, close_price)
                ON CONFLICT (timestamp_utc) DO UPDATE SET
                    symbol = EXCLUDED.symbol,
                    exchange = EXCLUDED.exchange,
                    timeframe = EXCLUDED.timeframe,
                    open_price = EXCLUDED.open_price,
                    high_price = EXCLUDED.high_price,
                    low_price = EXCLUDED.low_price,
                    close_price = EXCLUDED.close_price;";

            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.Add(new NpgsqlParameter("symbol", NpgsqlDbType.Varchar) { Value = symbol });
            cmd.Parameters.Add(new NpgsqlParameter("exchange", NpgsqlDbType.Varchar) { Value = exchange });
            cmd.Parameters.Add(new NpgsqlParameter("timeframe", NpgsqlDbType.Varchar) { Value = timeframe });
            cmd.Parameters.Add(new NpgsqlParameter("ts", NpgsqlDbType.Array | NpgsqlDbType.TimestampTz) { Value = ts });
            cmd.Parameters.Add(new NpgsqlParameter("openP", NpgsqlDbType.Array | NpgsqlDbType.Numeric) { Value = openP });
            cmd.Parameters.Add(new NpgsqlParameter("highP", NpgsqlDbType.Array | NpgsqlDbType.Numeric) { Value = highP });
            cmd.Parameters.Add(new NpgsqlParameter("lowP", NpgsqlDbType.Array | NpgsqlDbType.Numeric) { Value = lowP });
            cmd.Parameters.Add(new NpgsqlParameter("closeP", NpgsqlDbType.Array | NpgsqlDbType.Numeric) { Value = closeP });

            total += await cmd.ExecuteNonQueryAsync(ct);
        }

        return total;
    }

    // ── Delete rows ───────────────────────────────────────────────────────

    /// <summary>
    /// Delete rows from <paramref name="tableName"/>.
    /// When both <paramref name="startMs"/> and <paramref name="endMs"/> are
    /// <c>null</c>, the whole table is emptied via TRUNCATE and the row count
    /// observed before truncation is returned. When both are provided, a
    /// DELETE on the inclusive range is issued. If the table does not exist,
    /// returns 0 without throwing.
    /// </summary>
    public async Task<long> DeleteRowsAsync(
        string tableName, long? startMs, long? endMs, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return 0;
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);

        if (startMs is null && endMs is null)
        {
            var before = await conn.ExecuteScalarAsync<long>(new CommandDefinition(
                $@"SELECT COUNT(*)::bigint FROM ""{tbl}""", cancellationToken: ct));
            await conn.ExecuteAsync(new CommandDefinition(
                $@"TRUNCATE TABLE ""{tbl}""", cancellationToken: ct));
            return before;
        }

        if (startMs is null || endMs is null)
            throw new ArgumentException("startMs and endMs must both be null or both be provided");

        var affected = await conn.ExecuteAsync(new CommandDefinition(
            $@"DELETE FROM ""{tbl}""
               WHERE timestamp_utc >= @s AND timestamp_utc <= @e",
            new { s = ToUtc(startMs.Value), e = ToUtc(endMs.Value) },
            cancellationToken: ct));
        return affected;
    }

    // ── Ping ──────────────────────────────────────────────────────────────

    public Task<bool> PingAsync(CancellationToken ct = default) => _pg.PingAsync(ct);

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

    // ── Feature computation (SQL window functions) ────────────────────────

    /// <summary>
    /// Computes the 27 approved feature columns for an existing market-data
    /// table using a single SQL pass (CTE + UPDATE ... FROM). For tables that
    /// were created before the feature schema was introduced, missing columns
    /// are first added via <c>ALTER TABLE ... ADD COLUMN IF NOT EXISTS</c>
    /// (idempotent). Returns the number of updated rows.
    /// </summary>
    public async Task<long> ComputeAndUpdateFeaturesSinceAsync(
        string tableName, long updateFromMs, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        await EnsureFeatureColumnsAsync(conn, tbl, ct);

        var (selectList, setList) = BuildFeatureSqlFragments();
        var lookbackOffset = Math.Max(0, GetFeatureLookbackRows() - 1);

        var updateSql = $@"
            WITH bounds AS (
                SELECT COALESCE((
                    SELECT timestamp_utc
                    FROM ""{tbl}""
                    WHERE timestamp_utc < @update_from
                    ORDER BY timestamp_utc DESC
                    OFFSET @lookback_offset LIMIT 1
                ), @update_from::timestamptz) AS seed_from
            ),
            source_rows AS (
                SELECT *
                FROM ""{tbl}""
                WHERE timestamp_utc >= (SELECT seed_from FROM bounds)
            ),
            tr_prep AS (
                SELECT
                    timestamp_utc,
                    GREATEST(
                        (high_price::double precision - low_price::double precision),
                        ABS(high_price::double precision - LAG(close_price::double precision, 1) OVER w),
                        ABS(low_price::double precision  - LAG(close_price::double precision, 1) OVER w)
                    )::double precision AS tr_raw
                FROM source_rows
                WINDOW w AS (PARTITION BY symbol, timeframe ORDER BY timestamp_utc)
            ),
            cte AS (
                SELECT
                    timestamp_utc,
                    {selectList}
                FROM source_rows
                JOIN tr_prep USING (timestamp_utc)
                WINDOW w AS (PARTITION BY symbol, timeframe ORDER BY timestamp_utc)
            )
                        UPDATE ""{tbl}"" AS t
            SET {setList}
            FROM cte
            WHERE t.timestamp_utc = cte.timestamp_utc
              AND t.timestamp_utc >= @update_from;";

        var affected = await conn.ExecuteAsync(new CommandDefinition(
            updateSql,
            new
            {
                update_from = ToUtc(updateFromMs),
                lookback_offset = lookbackOffset,
            },
            commandTimeout: NoTimeout,
            cancellationToken: ct));
        return affected;
    }

    public async Task<long> ComputeAndUpdateFeaturesAsync(
        string tableName, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);

        await EnsureFeatureColumnsAsync(conn, tbl, ct);
        var (selectList, setList) = BuildFeatureSqlFragments();

        var updateSql = $@"
            WITH tr_prep AS (
                SELECT
                    timestamp_utc,
                    GREATEST(
                        (high_price::double precision - low_price::double precision),
                        ABS(high_price::double precision - LAG(close_price::double precision, 1) OVER w),
                        ABS(low_price::double precision  - LAG(close_price::double precision, 1) OVER w)
                    )::double precision AS tr_raw
                FROM ""{tbl}""
                WINDOW w AS (PARTITION BY symbol, timeframe ORDER BY timestamp_utc)
            ),
            cte AS (
                SELECT
                    timestamp_utc,
                    {selectList}
                FROM ""{tbl}""
                JOIN tr_prep USING (timestamp_utc)
                WINDOW w AS (PARTITION BY symbol, timeframe ORDER BY timestamp_utc)
            )
            UPDATE ""{tbl}"" AS t
            SET {setList}
            FROM cte
            WHERE t.timestamp_utc = cte.timestamp_utc;";

        var affected = await conn.ExecuteAsync(new CommandDefinition(
            updateSql, commandTimeout: NoTimeout, cancellationToken: ct));
        return affected;
    }

    private static int GetFeatureLookbackRows()
    {
        var lookbackRows = 0;
        if (DatasetConstants.ReturnHorizons.Length > 0)
        {
            lookbackRows = Math.Max(lookbackRows, DatasetConstants.ReturnHorizons.Max());
        }

        if (DatasetConstants.RollingWindows.Length > 0)
        {
            lookbackRows = Math.Max(lookbackRows, DatasetConstants.RollingWindows.Max());
        }

        if (DatasetConstants.RsiLagSteps.Length > 0)
        {
            lookbackRows = Math.Max(lookbackRows, DatasetConstants.RsiLagSteps.Max());
        }

        return Math.Max(lookbackRows, 1);
    }

    private static async Task EnsureFeatureColumnsAsync(
        NpgsqlConnection conn,
        string tableName,
        CancellationToken ct)
    {
        var alterSb = new StringBuilder();
        foreach (var (col, sqlType) in DatasetConstants.FeatureTableSchema)
        {
            alterSb.Append("ALTER TABLE \"").Append(tableName)
                   .Append("\" ADD COLUMN IF NOT EXISTS \"")
                   .Append(col).Append("\" ").Append(sqlType).AppendLine(";");
        }

        await conn.ExecuteAsync(new CommandDefinition(
            alterSb.ToString(), commandTimeout: NoTimeout, cancellationToken: ct));
    }

    private static (string SelectList, string SetList) BuildFeatureSqlFragments()
    {
        const double TwoPi = 2.0 * Math.PI;
        var pi2 = TwoPi.ToString("R", System.Globalization.CultureInfo.InvariantCulture);

        static string Cast(string expr) => $"({expr})::double precision";
        static string PctChange(string col, int k) =>
            Cast($"{col}::double precision / NULLIF(LAG({col}::double precision, {k}) OVER w, 0) - 1");
        static string LogReturn(string col, int k) =>
            Cast($"LN(GREATEST({col}::double precision / NULLIF(LAG({col}::double precision, {k}) OVER w, 0), 1e-10))");
        static string Rolling(string agg, string col, int w) =>
            $"{agg}({col}::double precision) OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN {w - 1} PRECEDING AND CURRENT ROW)";

        var parts = new List<string>();
        foreach (var h in DatasetConstants.ReturnHorizons)
            parts.Add($"{PctChange("close_price", h)} AS return_{h}");
        foreach (var h in DatasetConstants.ReturnHorizons)
            parts.Add($"{LogReturn("close_price", h)} AS log_return_{h}");
        foreach (var w in DatasetConstants.RollingWindows)
        {
            parts.Add($"{Rolling("AVG", "close_price", w)} AS price_roll{w}_mean");
            parts.Add($"{Rolling("STDDEV_POP", "close_price", w)} AS price_roll{w}_std");
            parts.Add($"{Rolling("MIN", "close_price", w)} AS price_roll{w}_min");
            parts.Add($"{Rolling("MAX", "close_price", w)} AS price_roll{w}_max");
        }
        foreach (var w in DatasetConstants.RollingWindows)
            parts.Add(
                $"(close_price::double precision / NULLIF({Rolling("AVG", "close_price", w)}, 0))::double precision " +
                $"AS price_to_roll{w}_mean");
        foreach (var w in DatasetConstants.RollingWindows)
            parts.Add(
                $"({Rolling("STDDEV_POP", "close_price", w)} / NULLIF({Rolling("AVG", "close_price", w)}, 0))::double precision " +
                $"AS price_vol_{w}");
        foreach (var w in DatasetConstants.RollingWindows)
            parts.Add($"{Rolling("AVG", "open_interest", w)} AS oi_roll{w}_mean");
        parts.Add($"{PctChange("open_interest", 1)} AS oi_return_1");
        foreach (var k in DatasetConstants.RsiLagSteps)
            parts.Add($"LAG(rsi::double precision, {k}) OVER w AS rsi_lag_{k}");
        parts.Add($"SIN({pi2} * EXTRACT(HOUR FROM timestamp_utc AT TIME ZONE 'UTC') / 24.0)::double precision AS hour_sin");
        parts.Add($"COS({pi2} * EXTRACT(HOUR FROM timestamp_utc AT TIME ZONE 'UTC') / 24.0)::double precision AS hour_cos");
        parts.Add($"SIN({pi2} * EXTRACT(DOW  FROM timestamp_utc AT TIME ZONE 'UTC') / 7.0)::double precision  AS dow_sin");
        parts.Add($"COS({pi2} * EXTRACT(DOW  FROM timestamp_utc AT TIME ZONE 'UTC') / 7.0)::double precision  AS dow_cos");

        foreach (var w in DatasetConstants.RollingWindows)
        {
            parts.Add(
                $"AVG(tr_raw) OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc " +
                $"ROWS BETWEEN {w - 1} PRECEDING AND CURRENT ROW)::double precision AS atr_{w}");
        }

        parts.Add(
            $"ABS(close_price::double precision - open_price::double precision)::double precision " +
            $"AS candle_body");
        parts.Add(
            $"(high_price::double precision - GREATEST(close_price::double precision, open_price::double precision))::double precision " +
            $"AS upper_wick");
        parts.Add(
            $"(LEAST(close_price::double precision, open_price::double precision) - low_price::double precision)::double precision " +
            $"AS lower_wick");

        foreach (var w in DatasetConstants.RollingWindows)
            parts.Add($"{Rolling("AVG", "volume", w)} AS volume_roll{w}_mean");
        foreach (var w in DatasetConstants.RollingWindows)
            parts.Add(
                $"(volume::double precision / NULLIF({Rolling("AVG", "volume", w)}, 0))::double precision " +
                $"AS volume_to_roll{w}_mean");
        parts.Add($"{PctChange("volume", 1)} AS volume_return_1");
        parts.Add($"(rsi::double precision - LAG(rsi::double precision, 1) OVER w)::double precision AS rsi_slope");

        var selectList = string.Join(",\n                ", parts);
        var featureCols = DatasetConstants.FeatureTableSchema.Select(f => f.Column).ToList();
        var setList = string.Join(
            ",\n                ",
            featureCols.Select(c => $"\"{c}\" = cte.\"{c}\""));
        return (selectList, setList);
    }
}

internal static class DictionaryExtensions
{
    public static IReadOnlyDictionary<K, V> AsReadOnly<K, V>(this IDictionary<K, V> d)
        where K : notnull => new System.Collections.ObjectModel.ReadOnlyDictionary<K, V>(d);
}
