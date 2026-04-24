using System.Text;
using System.Text.RegularExpressions;
using Dapper;
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
///   index_price     NUMERIC
///   funding_rate    NUMERIC
///   open_interest   NUMERIC
///   rsi             NUMERIC
///
/// All timestamps crossing the Kafka boundary are epoch milliseconds.
/// </summary>
public sealed class DatasetRepository
{
    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<DatasetRepository> _log;

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
    /// Create the market-data table with the canonical 8-column schema if it
    /// does not yet exist. Idempotent.
    /// </summary>
    public async Task CreateTableIfNotExistsAsync(string tableName, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        var sql = $@"
            CREATE TABLE IF NOT EXISTS ""{tbl}"" (
                timestamp_utc  TIMESTAMP WITH TIME ZONE PRIMARY KEY,
                symbol         VARCHAR    NOT NULL,
                exchange       VARCHAR    NOT NULL,
                timeframe      VARCHAR    NOT NULL,
                index_price    NUMERIC,
                funding_rate   NUMERIC,
                open_interest  NUMERIC,
                rsi            NUMERIC
            );";
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, cancellationToken: ct));
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
        string tableName, long startMs, long endMs, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync(new CommandDefinition(
            $@"SELECT
                 (EXTRACT(EPOCH FROM timestamp_utc) * 1000)::bigint AS timestamp_ms,
                 symbol, exchange, timeframe,
                 index_price, funding_rate, open_interest, rsi
               FROM ""{tbl}""
               WHERE timestamp_utc >= @s AND timestamp_utc <= @e
               ORDER BY timestamp_utc",
            new { s = ToUtc(startMs), e = ToUtc(endMs) }, cancellationToken: ct));
        return rows.Select(r => (IReadOnlyDictionary<string, object?>)
            ((IDictionary<string, object?>)r).AsReadOnly()).ToList();
    }

    // ── CSV export ────────────────────────────────────────────────────────

    public async Task<byte[]> ExportCsvAsync(
        string tableName, long startMs, long endMs, CancellationToken ct = default)
    {
        var rows = await FetchRowsAsync(tableName, startMs, endMs, ct);
        if (rows.Count == 0) return "no data\n"u8.ToArray();

        var cols = rows[0].Keys.ToArray();
        var sb = new StringBuilder();
        sb.AppendLine(string.Join(",", cols));
        foreach (var row in rows)
        {
            var values = cols.Select(c =>
            {
                var v = row[c];
                if (v is null) return "";
                if (c == "timestamp_ms" && v is long lv)
                    return DateTimeOffset.FromUnixTimeMilliseconds(lv).ToString("o");
                return v.ToString() ?? "";
            });
            sb.AppendLine(string.Join(",", values));
        }
        return Encoding.UTF8.GetBytes(sb.ToString());
    }

    // ── Bulk upsert ───────────────────────────────────────────────────────

    public sealed record MarketRow(
        long   TimestampMs,
        string Symbol,
        string Exchange,
        string Timeframe,
        decimal? IndexPrice,
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

            var ts     = new DateTime[n];
            var sym    = new string[n];
            var exch   = new string[n];
            var tf     = new string[n];
            var price  = new decimal?[n];
            var fund   = new decimal?[n];
            var oi     = new decimal?[n];
            var rsi    = new decimal?[n];

            for (int i = 0; i < n; i++)
            {
                var r = slice[i];
                ts[i]    = ToUtc(r.TimestampMs);
                sym[i]   = r.Symbol;
                exch[i]  = r.Exchange;
                tf[i]    = r.Timeframe;
                price[i] = r.IndexPrice;
                fund[i]  = r.FundingRate;
                oi[i]    = r.OpenInterest;
                rsi[i]   = r.Rsi;
            }

            var sql = $@"
                INSERT INTO ""{tbl}"" (
                    timestamp_utc, symbol, exchange, timeframe,
                    index_price, funding_rate, open_interest, rsi
                )
                SELECT * FROM UNNEST (
                    @ts::timestamptz[],
                    @sym::varchar[],
                    @exch::varchar[],
                    @tf::varchar[],
                    @price::numeric[],
                    @fund::numeric[],
                    @oi::numeric[],
                    @rsi::numeric[]
                )
                ON CONFLICT (timestamp_utc) DO UPDATE SET
                    symbol        = EXCLUDED.symbol,
                    exchange      = EXCLUDED.exchange,
                    timeframe     = EXCLUDED.timeframe,
                    index_price   = EXCLUDED.index_price,
                    funding_rate  = EXCLUDED.funding_rate,
                    open_interest = EXCLUDED.open_interest,
                    rsi           = EXCLUDED.rsi;";

            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.Add(new NpgsqlParameter("ts",    NpgsqlDbType.Array | NpgsqlDbType.TimestampTz) { Value = ts });
            cmd.Parameters.Add(new NpgsqlParameter("sym",   NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = sym });
            cmd.Parameters.Add(new NpgsqlParameter("exch",  NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = exch });
            cmd.Parameters.Add(new NpgsqlParameter("tf",    NpgsqlDbType.Array | NpgsqlDbType.Varchar)     { Value = tf });
            cmd.Parameters.Add(new NpgsqlParameter("price", NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = price });
            cmd.Parameters.Add(new NpgsqlParameter("fund",  NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = fund });
            cmd.Parameters.Add(new NpgsqlParameter("oi",    NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = oi });
            cmd.Parameters.Add(new NpgsqlParameter("rsi",   NpgsqlDbType.Array | NpgsqlDbType.Numeric)     { Value = rsi });

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
        decimal? Min,
        decimal? Max,
        decimal? Mean,
        decimal? Std);

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
    public async Task<ColumnStatsResult?> GetColumnStatsAsync(
        string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return null;
        var tbl = Safe(tableName);

        await using var conn = await _pg.OpenAsync(ct);

        var cols = (await conn.QueryAsync<(string column_name, string data_type)>(
            new CommandDefinition(
                @"SELECT column_name, data_type
                  FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = @tbl
                  ORDER BY ordinal_position",
                new { tbl }, cancellationToken: ct)))
            .Select(r => new ColumnInfo(r.column_name, r.data_type))
            .ToList();

        if (cols.Count == 0) return new ColumnStatsResult(0, Array.Empty<ColumnStat>());

        // Build one big SELECT with aggregations for every column.
        var sb = new StringBuilder();
        sb.Append("SELECT COUNT(*)::bigint AS total_rows");
        foreach (var c in cols)
        {
            var safeCol = Safe(c.Name);
            sb.Append($@", COUNT(""{safeCol}"")::bigint AS ""nn_{safeCol}""");
            if (_numericTypes.Contains(c.DataType))
            {
                sb.Append($@", MIN(""{safeCol}"")::numeric AS ""min_{safeCol}""");
                sb.Append($@", MAX(""{safeCol}"")::numeric AS ""max_{safeCol}""");
                sb.Append($@", AVG(""{safeCol}"")::numeric AS ""avg_{safeCol}""");
                sb.Append($@", STDDEV_POP(""{safeCol}"")::numeric AS ""std_{safeCol}""");
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
            decimal? min = null, max = null, mean = null, std = null;
            if (_numericTypes.Contains(c.DataType))
            {
                min  = ToDec(dict, $"min_{safeCol}");
                max  = ToDec(dict, $"max_{safeCol}");
                mean = ToDec(dict, $"avg_{safeCol}");
                std  = ToDec(dict, $"std_{safeCol}");
            }
            stats.Add(new ColumnStat(c.Name, c.DataType, nn, min, max, mean, std));
        }

        return new ColumnStatsResult(totalRows, stats);
    }

    private static decimal? ToDec(IDictionary<string, object?> d, string key) =>
        d.TryGetValue(key, out var v) && v is not null ? Convert.ToDecimal(v) : null;

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
}

internal static class DictionaryExtensions
{
    public static IReadOnlyDictionary<K, V> AsReadOnly<K, V>(this IDictionary<K, V> d)
        where K : notnull => new System.Collections.ObjectModel.ReadOnlyDictionary<K, V>(d);
}
