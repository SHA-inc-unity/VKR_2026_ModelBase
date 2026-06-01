using System.Text;
using Dapper;
using Npgsql;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
    // ── Clean: PREVIEW (counts only, no mutations) ───────────────────────

    public async Task<long> CountDuplicatesAsync(string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return 0;
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        return await conn.ExecuteScalarAsync<long>(new CommandDefinition(
            $@"SELECT COALESCE(SUM(cnt - 1), 0)::bigint
               FROM (SELECT COUNT(*)::bigint AS cnt FROM ""{tbl}""
                     GROUP BY timestamp_utc HAVING COUNT(*) > 1) s",
            cancellationToken: ct));
    }

    public async Task<long> CountOhlcViolationsAsync(string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return 0;
        var tbl  = Safe(tableName);
        var cols = await GetColumnNamesAsync(tbl, ct);
        if (!_ohlcCols.All(cols.Contains)) return 0;

        await using var conn = await _pg.OpenAsync(ct);
        return await conn.ExecuteScalarAsync<long>(new CommandDefinition(
            $@"SELECT COUNT(*)::bigint FROM ""{tbl}""
               WHERE high_price IS NOT NULL AND low_price IS NOT NULL
                 AND open_price IS NOT NULL AND close_price IS NOT NULL
                 AND (high_price < GREATEST(open_price, close_price, low_price)
                      OR low_price > LEAST(open_price, close_price, high_price))",
            cancellationToken: ct));
    }

    public async Task<long> CountZeroStreakRowsAsync(
        string tableName, int minLen = 3, CancellationToken ct = default)
    {
        var rows = await DetectZeroStreaksAsync(tableName, minLen, ct);
        return rows.Count;
    }

    public async Task<long> CountGapsAsync(
        string tableName, long stepMs, CancellationToken ct = default)
    {
        var rows = await DetectGapsAsync(tableName, stepMs, ct);
        return rows.Count;
    }

    // ── Clean: APPLY (mutations) ─────────────────────────────────────────

    /// <summary>
    /// Drop duplicate rows by ctid using the given strategy:
    ///   "first" (default) keeps the first row, deletes the rest;
    ///   "last"            keeps the last row;
    ///   "none"            deletes every duplicated timestamp entirely.
    /// Returns the number of deleted rows.
    /// </summary>
    public async Task<long> ApplyDropDuplicatesAsync(
        string tableName, NpgsqlConnection conn,
        string strategy = "first", CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        strategy = (strategy ?? "first").Trim().ToLowerInvariant();
        return strategy switch
        {
            // "Keep none" wipes every timestamp that appears 2+ times.
            "none" => await conn.ExecuteAsync(new CommandDefinition($@"
                DELETE FROM ""{tbl}"" t
                USING (
                    SELECT timestamp_utc FROM ""{tbl}""
                    GROUP BY timestamp_utc HAVING COUNT(*) > 1
                ) d
                WHERE t.timestamp_utc = d.timestamp_utc", cancellationToken: ct)),
            // "Last" — keep the row with the highest ctid.
            "last" => await conn.ExecuteAsync(new CommandDefinition($@"
                DELETE FROM ""{tbl}"" t
                USING (
                    SELECT ctid,
                           ROW_NUMBER() OVER (PARTITION BY timestamp_utc ORDER BY ctid DESC) AS rn
                    FROM ""{tbl}""
                ) d
                WHERE t.ctid = d.ctid AND d.rn > 1", cancellationToken: ct)),
            // Default — keep first.
            _ => await conn.ExecuteAsync(new CommandDefinition($@"
                DELETE FROM ""{tbl}"" t
                USING (
                    SELECT ctid,
                           ROW_NUMBER() OVER (PARTITION BY timestamp_utc ORDER BY ctid) AS rn
                    FROM ""{tbl}""
                ) d
                WHERE t.ctid = d.ctid AND d.rn > 1", cancellationToken: ct)),
        };
    }

    /// <summary>
    /// Recompute high_price = GREATEST(o, h, l, c) and low_price = LEAST(...)
    /// when an OHLC violation exists. Never deletes rows.
    /// </summary>
    public async Task<long> ApplyFixOhlcAsync(
        string tableName, NpgsqlConnection conn, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        // Skip silently for legacy tables that don't carry OHLC columns.
        var existing = (await conn.QueryAsync<string>(new CommandDefinition(
            @"SELECT column_name FROM information_schema.columns
              WHERE table_schema = 'public' AND table_name = @tbl",
            new { tbl }, cancellationToken: ct)))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        if (!_ohlcCols.All(existing.Contains)) return 0;

        return await conn.ExecuteAsync(new CommandDefinition($@"
            UPDATE ""{tbl}""
            SET high_price = GREATEST(open_price, high_price, low_price, close_price),
                low_price  = LEAST   (open_price, high_price, low_price, close_price)
            WHERE high_price IS NOT NULL AND low_price IS NOT NULL
              AND open_price IS NOT NULL AND close_price IS NOT NULL
              AND (high_price < GREATEST(open_price, close_price, low_price)
                   OR low_price > LEAST(open_price, close_price, high_price))",
            cancellationToken: ct));
    }

    /// <summary>
    /// Forward-fill zero/null values in <paramref name="column"/> using the
    /// last non-zero / non-null observation. Implemented as a single CTE
    /// + UPDATE.
    /// </summary>
    public async Task<long> ApplyFillZeroStreakAsync(
        string tableName, string column,
        NpgsqlConnection conn, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        var col = Safe(column);
        return await conn.ExecuteAsync(new CommandDefinition($@"
            WITH ranked AS (
                SELECT timestamp_utc,
                       ""{col}"" AS v,
                       SUM(CASE WHEN ""{col}"" IS NOT NULL AND ""{col}"" <> 0 THEN 1 ELSE 0 END)
                           OVER (ORDER BY timestamp_utc ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS grp
                FROM ""{tbl}""
            ),
            filled AS (
                SELECT timestamp_utc,
                       FIRST_VALUE(v) OVER (PARTITION BY grp ORDER BY timestamp_utc
                                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS new_v
                FROM ranked
            )
            UPDATE ""{tbl}"" AS t
            SET ""{col}"" = f.new_v
            FROM filled f
            WHERE t.timestamp_utc = f.timestamp_utc
              AND f.new_v IS NOT NULL
              AND (t.""{col}"" IS NULL OR t.""{col}"" = 0)",
            cancellationToken: ct));
    }

    /// <summary>
    /// Delete rows whose timestamps appear in <paramref name="tsMs"/>.
    /// </summary>
    public async Task<long> ApplyDeleteByTimestampsAsync(
        string tableName, IReadOnlyList<long> tsMs,
        NpgsqlConnection conn, CancellationToken ct = default)
    {
        if (tsMs.Count == 0) return 0;
        var tbl = Safe(tableName);
        var arr = tsMs.Select(ToUtc).ToArray();
        await using var cmd = new NpgsqlCommand(
            $@"DELETE FROM ""{tbl}"" WHERE timestamp_utc = ANY(@ts::timestamptz[])", conn);
        cmd.Parameters.Add(new NpgsqlParameter("ts",
            NpgsqlTypes.NpgsqlDbType.Array | NpgsqlTypes.NpgsqlDbType.TimestampTz) { Value = arr });
        return await cmd.ExecuteNonQueryAsync(ct);
    }

    /// <summary>
    /// Insert synthetic rows for missing timestamps inside the observed range.
    /// <paramref name="method"/> = "linear" | "forward_fill". Numeric columns
    /// are filled by linear interpolation between neighbours (linear) or by
    /// the previous observed value (forward_fill). symbol/exchange/timeframe
    /// inherit from the previous row.
    /// </summary>
    public async Task<long> ApplyFillGapsAsync(
        string tableName, long stepMs, string method,
        NpgsqlConnection conn, CancellationToken ct = default)
    {
        var tbl = Safe(tableName);
        method = (method ?? "forward_fill").Trim().ToLowerInvariant();
        if (method != "linear" && method != "forward_fill")
            throw new ArgumentException($"unknown interpolation method: {method}");

        var cols = (await conn.QueryAsync<(string column_name, string data_type)>(
            new CommandDefinition(
                @"SELECT column_name, data_type FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = @tbl
                  ORDER BY ordinal_position",
                new { tbl }, cancellationToken: ct))).ToList();
        if (cols.Count == 0) return 0;

        // Build per-column expressions:
        //   timestamp_utc → g.ts
        //   numeric       → linear: prev + (next - prev) * frac, or forward: prev
        //   text          → forward (prev)
        var numeric = new HashSet<string>(_numericTypes, StringComparer.OrdinalIgnoreCase);
        var insertCols = new List<string>();
        var selectExprs = new List<string>();
        foreach (var c in cols)
        {
            var name = Safe(c.column_name);
            insertCols.Add($"\"{name}\"");
            if (name == "timestamp_utc")
            {
                selectExprs.Add("g.ts");
            }
            else if (numeric.Contains(c.data_type))
            {
                if (method == "linear")
                {
                    // (prv) / (nxt) parens are required because prv/nxt are
                    // composite-type CTE columns, not table aliases — without
                    // them PostgreSQL raises "missing FROM-clause entry for
                    // table 'prv'".
                    selectExprs.Add($@"
                        CASE WHEN (nxt).""{name}"" IS NOT NULL AND (prv).""{name}"" IS NOT NULL
                                  AND nxt_ts > prv_ts
                             THEN (prv).""{name}"" + ((nxt).""{name}"" - (prv).""{name}"")
                                  * EXTRACT(EPOCH FROM (g.ts - prv_ts))
                                  / NULLIF(EXTRACT(EPOCH FROM (nxt_ts - prv_ts)), 0)
                             ELSE COALESCE((prv).""{name}"", (nxt).""{name}"")
                        END");
                }
                else
                {
                    selectExprs.Add($"COALESCE((prv).\"{name}\", (nxt).\"{name}\")");
                }
            }
            else
            {
                selectExprs.Add($"COALESCE((prv).\"{name}\", (nxt).\"{name}\")");
            }
        }

        // Generate-series across the observed [min, max] range; for each
        // missing timestamp pick the previous (prv) and next (nxt) existing row
        // via LATERAL subqueries.
        var sql = $@"
            WITH bounds AS (
                SELECT MIN(timestamp_utc) AS lo, MAX(timestamp_utc) AS hi FROM ""{tbl}""
            ),
            grid AS (
                SELECT g.ts FROM bounds,
                  generate_series(bounds.lo, bounds.hi,
                      make_interval(secs => @step_s)) AS g(ts)
                WHERE NOT EXISTS (
                    SELECT 1 FROM ""{tbl}"" t WHERE t.timestamp_utc = g.ts
                )
            ),
            with_neighbours AS (
                SELECT g.ts,
                       prv.timestamp_utc AS prv_ts,
                       nxt.timestamp_utc AS nxt_ts,
                       prv,
                       nxt
                FROM grid g
                LEFT JOIN LATERAL (
                    SELECT * FROM ""{tbl}"" p
                    WHERE p.timestamp_utc < g.ts
                    ORDER BY p.timestamp_utc DESC LIMIT 1
                ) prv ON TRUE
                LEFT JOIN LATERAL (
                    SELECT * FROM ""{tbl}"" n
                    WHERE n.timestamp_utc > g.ts
                    ORDER BY n.timestamp_utc ASC LIMIT 1
                ) nxt ON TRUE
            )
            INSERT INTO ""{tbl}"" ({string.Join(", ", insertCols)})
            SELECT {string.Join(",\n                   ", selectExprs)}
            FROM with_neighbours g
            WHERE prv IS NOT NULL OR nxt IS NOT NULL
            ON CONFLICT (timestamp_utc) DO NOTHING";

        return await conn.ExecuteAsync(new CommandDefinition(
            sql, new { step_s = stepMs / 1000.0 }, cancellationToken: ct));
    }

    /// <summary>
    /// Acquire a session-level advisory lock keyed by the table name.
    /// Returns the connection that holds the lock; the caller must keep
    /// it open until the apply is fully committed.
    /// </summary>
    public async Task<NpgsqlConnection> AcquireApplyLockAsync(
        string tableName, CancellationToken ct = default)
    {
        var conn = await _pg.OpenAsync(ct);
        try
        {
            await conn.ExecuteAsync(new CommandDefinition(
                "SELECT pg_advisory_lock(hash_record_extended(ROW(@t), 0))",
                new { t = tableName }, cancellationToken: ct));
        }
        catch
        {
            await conn.DisposeAsync();
            throw;
        }
        return conn;
    }

    public async Task ReleaseApplyLockAsync(
        NpgsqlConnection conn, string tableName, CancellationToken ct = default)
    {
        try
        {
            await conn.ExecuteAsync(new CommandDefinition(
                "SELECT pg_advisory_unlock(hash_record_extended(ROW(@t), 0))",
                new { t = tableName }, cancellationToken: ct));
        }
        finally
        {
            await conn.DisposeAsync();
        }
    }
}
