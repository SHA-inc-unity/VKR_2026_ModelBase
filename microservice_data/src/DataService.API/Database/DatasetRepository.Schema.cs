using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using DataService.API.Dataset;
using Npgsql;
using NpgsqlTypes;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
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
}
