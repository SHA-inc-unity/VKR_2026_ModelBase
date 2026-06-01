using System.Text;
using Dapper;
using Npgsql;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
    // ── 1. Gaps in timestamp grid ────────────────────────────────────────
    public async Task<IReadOnlyList<AnomalyRow>> DetectGapsAsync(
        string tableName, long stepMs, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        // Only flag missing timestamps inside the observed [min, max] range.
        var rows = await conn.QueryAsync<DateTime>(new CommandDefinition(
            $@"WITH bounds AS (
                  SELECT MIN(timestamp_utc) AS lo, MAX(timestamp_utc) AS hi
                  FROM ""{tbl}""
              )
              SELECT g.ts
              FROM bounds, generate_series(bounds.lo, bounds.hi,
                  make_interval(secs => @step_s)) AS g(ts)
              WHERE NOT EXISTS (
                  SELECT 1 FROM ""{tbl}"" t WHERE t.timestamp_utc = g.ts
              )
              ORDER BY g.ts",
            new { step_s = stepMs / 1000.0 }, cancellationToken: ct));
        return rows.Select(d => new AnomalyRow(
            ToMs(d), "gap", "warning", null, null,
            "missing timestamp")).ToList();
    }

    // ── 2. Duplicate timestamps ──────────────────────────────────────────
    public async Task<IReadOnlyList<AnomalyRow>> DetectDuplicatesAsync(
        string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        // PostgreSQL's market-data tables have timestamp_utc as PK so duplicates
        // shouldn't normally occur, but if the schema was relaxed or rows were
        // imported via COPY without ON CONFLICT, this catches them.
        var rows = await conn.QueryAsync<(DateTime ts, long cnt)>(new CommandDefinition(
            $@"SELECT timestamp_utc AS ts, COUNT(*)::bigint AS cnt
               FROM ""{tbl}""
               GROUP BY timestamp_utc
               HAVING COUNT(*) > 1
               ORDER BY timestamp_utc",
            cancellationToken: ct));
        return rows.Select(r => new AnomalyRow(
            ToMs(r.ts), "duplicate", "critical", "timestamp_utc", r.cnt,
            $"{r.cnt} rows share this timestamp")).ToList();
    }

    // ── 3. OHLC violations ───────────────────────────────────────────────
    public async Task<IReadOnlyList<AnomalyRow>> DetectOhlcViolationsAsync(
        string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl  = Safe(tableName);
        // Tables can pre-date the OHLC columns (legacy schema) — bail out
        // silently if any of the four required columns is missing.
        var cols = await GetColumnNamesAsync(tbl, ct);
        if (!_ohlcCols.All(cols.Contains)) return Array.Empty<AnomalyRow>();

        await using var conn = await _pg.OpenAsync(ct);
        // Conditions: high < max(open, close, low) OR low > min(open, close, high).
                var rows = await conn.QueryAsync<(DateTime ts, double? o, double? h, double? l, double? c)>(
            new CommandDefinition(
                                $@"SELECT timestamp_utc AS ts,
                                                    open_price::double precision AS o,
                                                    high_price::double precision AS h,
                                                    low_price::double precision AS l,
                                                    close_price::double precision AS c
                   FROM ""{tbl}""
                   WHERE high_price IS NOT NULL AND low_price IS NOT NULL
                     AND open_price IS NOT NULL AND close_price IS NOT NULL
                     AND (high_price < GREATEST(open_price, close_price, low_price)
                          OR low_price > LEAST(open_price, close_price, high_price))
                   ORDER BY timestamp_utc",
                cancellationToken: ct));
        return rows.Select(r => new AnomalyRow(
            ToMs(r.ts), "ohlc_violation", "critical", "high_price",
            (double?)r.h,
            $"O={r.o} H={r.h} L={r.l} C={r.c}")).ToList();
    }

    // ── 4. Negative values in non-negative columns ───────────────────────
    public async Task<IReadOnlyList<AnomalyRow>> DetectNegativesAsync(
        string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl  = Safe(tableName);
        // Only check columns that actually exist on this table.
        var cols = await GetColumnNamesAsync(tbl, ct);
        var checks = _negativeCols.Where(cols.Contains).ToList();
        if (checks.Count == 0) return Array.Empty<AnomalyRow>();

        await using var conn = await _pg.OpenAsync(ct);
        var union = string.Join("\nUNION ALL\n", checks.Select(c =>
            $@"SELECT timestamp_utc AS ts, '{c}' AS col, ""{Safe(c)}""::double precision AS val
               FROM ""{tbl}"" WHERE ""{Safe(c)}"" < 0"));
        var rows = await conn.QueryAsync<(DateTime ts, string col, double val)>(
            new CommandDefinition(union + " ORDER BY ts", cancellationToken: ct));
        return rows.Select(r => new AnomalyRow(
            ToMs(r.ts), "negative_value", "critical", r.col, r.val,
            $"negative value in {r.col}")).ToList();
    }

    // ── 5. Zero / null streaks (≥ 3 consecutive) in OI / funding_rate ────
    public async Task<IReadOnlyList<AnomalyRow>> DetectZeroStreaksAsync(
        string tableName, int minLen = 3, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl  = Safe(tableName);
        var cols = await GetColumnNamesAsync(tbl, ct);
        var checks = _streakCols.Where(cols.Contains).ToList();
        if (checks.Count == 0) return Array.Empty<AnomalyRow>();

        await using var conn = await _pg.OpenAsync(ct);
        var result = new List<AnomalyRow>();
        foreach (var col in checks)
        {
            var safeCol = Safe(col);
            // is_bad = (col IS NULL OR col = 0). Group consecutive runs by
            // (row_number - sum of NOT bad), classic gaps-and-islands trick.
            var sql = $@"
                WITH flagged AS (
                    SELECT timestamp_utc,
                           ""{safeCol}"" AS v,
                           CASE WHEN ""{safeCol}"" IS NULL OR ""{safeCol}"" = 0 THEN 1 ELSE 0 END AS bad
                    FROM ""{tbl}""
                ),
                grouped AS (
                    SELECT timestamp_utc, v, bad,
                           ROW_NUMBER() OVER (ORDER BY timestamp_utc)
                           - ROW_NUMBER() OVER (PARTITION BY bad ORDER BY timestamp_utc) AS grp
                    FROM flagged
                )
                SELECT timestamp_utc, COUNT(*) OVER (PARTITION BY grp) AS streak
                FROM grouped
                WHERE bad = 1
                  AND grp IN (
                      SELECT grp FROM grouped WHERE bad = 1
                      GROUP BY grp HAVING COUNT(*) >= @minLen
                  )
                ORDER BY timestamp_utc";
            var rows = await conn.QueryAsync<(DateTime ts, long streak)>(
                new CommandDefinition(sql, new { minLen }, cancellationToken: ct));
            result.AddRange(rows.Select(r => new AnomalyRow(
                ToMs(r.ts), "zero_streak", "warning", col, 0.0,
                $"zero/null streak of {r.streak} in {col}")));
        }
        return result;
    }

    // ── 6. IQR + Z-score outliers (single CTE) ───────────────────────────
    public async Task<IReadOnlyList<AnomalyRow>> DetectStatisticalOutliersAsync(
        string tableName, double zThreshold = 3.0, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl  = Safe(tableName);
        var cols = await GetColumnNamesAsync(tbl, ct);
        var checks = _outlierCols.Where(cols.Contains).ToList();
        if (checks.Count == 0) return Array.Empty<AnomalyRow>();

        await using var conn = await _pg.OpenAsync(ct);
        var result = new List<AnomalyRow>();
        foreach (var col in checks)
        {
            var safeCol = Safe(col);
            // One CTE: bounds (q1, q3, mean, std) → flag rows with both rules.
            var sql = $@"
                WITH bounds AS (
                    SELECT
                        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY ""{safeCol}"") AS q1,
                        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY ""{safeCol}"") AS q3,
                        AVG(""{safeCol}"")::double precision      AS mean_v,
                        STDDEV_SAMP(""{safeCol}"")::double precision AS std_v
                    FROM ""{tbl}""
                    WHERE ""{safeCol}"" IS NOT NULL
                )
                SELECT t.timestamp_utc,
                       t.""{safeCol}""::double precision AS v,
                       CASE
                           WHEN t.""{safeCol}"" < (b.q1 - 1.5 * (b.q3 - b.q1))
                             OR t.""{safeCol}"" > (b.q3 + 1.5 * (b.q3 - b.q1)) THEN 'iqr'
                           WHEN b.std_v > 0 AND ABS((t.""{safeCol}"" - b.mean_v) / b.std_v) >= @z THEN 'zscore'
                           ELSE NULL
                       END AS reason,
                       CASE WHEN b.std_v > 0
                            THEN ABS((t.""{safeCol}"" - b.mean_v) / b.std_v)
                            ELSE 0 END AS z
                FROM ""{tbl}"" t, bounds b
                WHERE t.""{safeCol}"" IS NOT NULL
                  AND ((t.""{safeCol}"" < (b.q1 - 1.5 * (b.q3 - b.q1)))
                    OR (t.""{safeCol}"" > (b.q3 + 1.5 * (b.q3 - b.q1)))
                    OR (b.std_v > 0 AND ABS((t.""{safeCol}"" - b.mean_v) / b.std_v) >= @z))
                ORDER BY t.timestamp_utc";
            var rows = await conn.QueryAsync<(DateTime ts, double v, string reason, double z)>(
                new CommandDefinition(sql, new { z = zThreshold }, cancellationToken: ct));
            foreach (var r in rows)
            {
                var typeStr = r.reason == "zscore" ? "zscore" : "iqr";
                result.Add(new AnomalyRow(
                    ToMs(r.ts), typeStr, "warning", col, r.v,
                    typeStr == "zscore"
                        ? $"|z|={r.z:F2} (threshold {zThreshold})"
                        : "outside Tukey fence (1.5·IQR)"));
            }
        }
        return result;
    }

    // ── 7. Rolling Z-score / IQR on log-returns of close (close_price) ──
    /// <summary>
    /// Detect price spikes via rolling Z-score (or IQR) on log-returns of
    /// <paramref name="column"/> (default <c>close_price</c>) within a sliding
    /// window of <paramref name="window"/> bars. <paramref name="mode"/> is
    /// "zscore" (compares |z| to <paramref name="threshold"/> in σ) or "iqr"
    /// (compares the return to the Tukey fence with k=<paramref name="threshold"/>).
    /// </summary>
    /// <remarks>
    /// We look at log-returns rather than raw price because returns are far
    /// more stationary; rolling stats over price would drift heavily on
    /// trending markets. The window is shifted by 1 (rows BETWEEN window
    /// PRECEDING AND 1 PRECEDING) so the current bar does not dilute its own
    /// score — critical for unbiased anomaly flagging.
    /// </remarks>
    public async Task<IReadOnlyList<AnomalyRow>> DetectRollingZScoreAsync(
        string tableName, string column, int window, double threshold, string mode,
        CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl = Safe(tableName);
        var col = Safe(column);
        var cols = await GetColumnNamesAsync(tbl, ct);
        if (!cols.Contains(column)) return Array.Empty<AnomalyRow>();
        if (window < 5)        window = 5;
        if (window > 5000)     window = 5000;
        if (threshold <= 0.0)  threshold = 4.5;
        var isIqr = string.Equals(mode, "iqr", StringComparison.OrdinalIgnoreCase);

        await using var conn = await _pg.OpenAsync(ct);

        IEnumerable<(DateTime ts, double v, double r,
            double? mean_r, double? std_r, double? q1, double? q3)> rows;

        if (!isIqr)
        {
            // Z-score mode: use native O(n) window functions — AVG and
            // STDDEV_SAMP support incremental sliding aggregates in PostgreSQL
            // and scale to millions of rows without memory pressure.
            // window is an already-validated int (5–5000), so string
            // interpolation here is safe from SQL injection.
            var sqlZscore = $@"
                WITH base AS (
                    SELECT timestamp_utc,
                           ""{col}""::double precision AS v,
                           LAG(""{col}""::double precision) OVER (ORDER BY timestamp_utc) AS prv
                    FROM ""{tbl}""
                    WHERE ""{col}"" IS NOT NULL AND ""{col}"" > 0
                ),
                ret AS (
                    SELECT timestamp_utc, v,
                           CASE WHEN prv IS NOT NULL AND prv > 0
                                THEN LN(v / prv) ELSE NULL END AS r
                    FROM base
                ),
                stats AS (
                    SELECT timestamp_utc, v, r,
                           AVG(r) OVER (ORDER BY timestamp_utc
                                        ROWS BETWEEN {window} PRECEDING AND 1 PRECEDING) AS mean_r,
                           STDDEV_SAMP(r) OVER (ORDER BY timestamp_utc
                                                ROWS BETWEEN {window} PRECEDING AND 1 PRECEDING) AS std_r
                    FROM ret
                )
                SELECT timestamp_utc, v, r, mean_r, std_r,
                       NULL::double precision AS q1_r,
                       NULL::double precision AS q3_r
                FROM stats
                WHERE r IS NOT NULL
                  AND std_r IS NOT NULL AND std_r > 0
                  AND ABS((r - mean_r) / std_r) >= @thr
                ORDER BY timestamp_utc";

            rows = await conn.QueryAsync<(DateTime ts, double v, double r,
                double? mean_r, double? std_r, double? q1, double? q3)>(
                new CommandDefinition(sqlZscore, new { thr = threshold },
                    commandTimeout: 0, cancellationToken: ct));
        }
        else
        {
            // IQR mode: PERCENTILE_CONT has no window-function equivalent in
            // PostgreSQL, so LATERAL is unavoidable. Guard with a row-count
            // check to prevent O(n × window) memory exhaustion on large tables.
            var rowCount = await conn.ExecuteScalarAsync<long>(
                new CommandDefinition($@"SELECT COUNT(*) FROM ""{tbl}""",
                    commandTimeout: 0, cancellationToken: ct));
            if (rowCount > 500_000)
            {
                _log.LogWarning(
                    "DetectRollingZScore IQR skipped for {Table}: {RowCount} rows exceeds the 500k limit for LATERAL approach",
                    tbl, rowCount);
                return Array.Empty<AnomalyRow>();
            }

            var sqlIqr = $@"
                WITH base AS (
                    SELECT timestamp_utc,
                           ""{col}""::double precision AS v,
                           LAG(""{col}""::double precision) OVER (ORDER BY timestamp_utc) AS prv
                    FROM ""{tbl}""
                    WHERE ""{col}"" IS NOT NULL AND ""{col}"" > 0
                ),
                ret AS (
                    SELECT timestamp_utc, v,
                           CASE WHEN prv IS NOT NULL AND prv > 0
                                THEN LN(v / prv) ELSE NULL END AS r
                    FROM base
                ),
                stats AS (
                    SELECT r1.timestamp_utc, r1.v, r1.r,
                           sub.mean_r, sub.std_r, sub.q1_r, sub.q3_r
                    FROM ret r1
                    LEFT JOIN LATERAL (
                        SELECT AVG(w.r)                                          AS mean_r,
                               STDDEV_SAMP(w.r)                                  AS std_r,
                               PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY w.r) AS q1_r,
                               PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY w.r) AS q3_r
                        FROM (
                            SELECT r2.r
                            FROM ret r2
                            WHERE r2.timestamp_utc < r1.timestamp_utc
                              AND r2.r IS NOT NULL
                            ORDER BY r2.timestamp_utc DESC
                            LIMIT @win
                        ) w
                    ) sub ON true
                )
                SELECT timestamp_utc, v, r, mean_r, std_r, q1_r, q3_r
                FROM stats
                WHERE r IS NOT NULL
                  AND q3_r IS NOT NULL AND q1_r IS NOT NULL
                  AND (q3_r - q1_r) > 0
                  AND (r > q3_r + @thr * (q3_r - q1_r) OR r < q1_r - @thr * (q3_r - q1_r))
                ORDER BY timestamp_utc";

            rows = await conn.QueryAsync<(DateTime ts, double v, double r,
                double? mean_r, double? std_r, double? q1, double? q3)>(
                new CommandDefinition(sqlIqr, new { win = window, thr = threshold },
                    commandTimeout: 0, cancellationToken: ct));
        }

        var typeStr = isIqr ? "rolling_iqr" : "rolling_zscore";
        return rows.Select(x =>
        {
            string detail;
            if (isIqr)
            {
                detail = $"return r={x.r:F4} outside Tukey fence (k={threshold}, IQR={(x.q3 ?? 0) - (x.q1 ?? 0):F4})";
            }
            else
            {
                var z = (x.std_r ?? 0) > 0 ? Math.Abs((x.r - (x.mean_r ?? 0)) / x.std_r!.Value) : 0.0;
                detail = $"|z|={z:F2} (window={window}, threshold={threshold}σ)";
            }
            return new AnomalyRow(ToMs(x.ts), typeStr, "warning", column, x.v, detail);
        }).ToList();
    }

    // ── 8. Frozen / stale price (≥ N consecutive identical close values) ──
    /// <summary>
    /// Flag <paramref name="minLen"/>+ consecutive bars where
    /// <paramref name="column"/> is identical and <c>volume &gt; 0</c>.
    /// Severity is "critical" — frozen feeds break any lag-feature.
    /// </summary>
    public async Task<IReadOnlyList<AnomalyRow>> DetectStalePriceAsync(
        string tableName, string column, int minLen,
        CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl = Safe(tableName);
        var col = Safe(column);
        var cols = await GetColumnNamesAsync(tbl, ct);
        if (!cols.Contains(column)) return Array.Empty<AnomalyRow>();
        if (minLen < 2) minLen = 2;
        var hasVolume = cols.Contains("volume");

        await using var conn = await _pg.OpenAsync(ct);
        // Group-by-runs trick: subtract dense_rank by (col, lag-of-col-changed)
        // to bucket consecutive identical values into runs.
        var volPredicate = hasVolume ? "AND volume > 0" : "";
        var sql = $@"
            WITH ordered AS (
                SELECT timestamp_utc,
                       ""{col}"" AS v
                       {(hasVolume ? ", volume" : "")}
                FROM ""{tbl}""
                WHERE ""{col}"" IS NOT NULL {volPredicate}
            ),
            flagged AS (
                SELECT timestamp_utc, v
                       {(hasVolume ? ", volume" : "")},
                       CASE WHEN v <> COALESCE(LAG(v) OVER (ORDER BY timestamp_utc), v + 1)
                            THEN 1 ELSE 0 END AS is_change
                FROM ordered
            ),
            runs AS (
                SELECT timestamp_utc, v,
                       SUM(is_change) OVER (ORDER BY timestamp_utc
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS run_id
                FROM flagged
            )
            SELECT timestamp_utc, v::double precision AS v, COUNT(*) OVER (PARTITION BY run_id) AS run_len
            FROM runs
            WHERE run_id IN (
                SELECT run_id FROM runs GROUP BY run_id HAVING COUNT(*) >= @minLen
            )
            ORDER BY timestamp_utc";

        var rows = await conn.QueryAsync<(DateTime ts, double v, long run_len)>(
            new CommandDefinition(sql, new { minLen }, cancellationToken: ct));
        return rows.Select(x => new AnomalyRow(
            ToMs(x.ts), "stale_price", "critical", column, x.v,
            $"frozen run length {x.run_len} (≥ {minLen})")).ToList();
    }

    // ── 9. Return outlier — |Δ/prev| × 100% above adaptive threshold ─────
    /// <summary>
    /// Flag bars whose absolute pct-change of <paramref name="column"/>
    /// exceeds <paramref name="thresholdPct"/> (e.g. 15.0 for 15 %). Severity
    /// is "warning" if &lt; 2× threshold, "critical" otherwise.
    /// </summary>
    public async Task<IReadOnlyList<AnomalyRow>> DetectReturnOutliersAsync(
        string tableName, string column, double thresholdPct,
        CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl = Safe(tableName);
        var col = Safe(column);
        var cols = await GetColumnNamesAsync(tbl, ct);
        if (!cols.Contains(column)) return Array.Empty<AnomalyRow>();
        if (thresholdPct <= 0) thresholdPct = 15.0;

        await using var conn = await _pg.OpenAsync(ct);
        var sql = $@"
            WITH base AS (
                SELECT timestamp_utc,
                       ""{col}""::double precision AS v,
                       LAG(""{col}""::double precision) OVER (ORDER BY timestamp_utc) AS prv
                FROM ""{tbl}""
                WHERE ""{col}"" IS NOT NULL
            )
            SELECT timestamp_utc, v, prv,
                   ABS((v - prv) / NULLIF(prv, 0)) * 100.0 AS pct
            FROM base
            WHERE prv IS NOT NULL AND prv <> 0
              AND ABS((v - prv) / prv) * 100.0 >= @thr
            ORDER BY timestamp_utc";

        var rows = await conn.QueryAsync<(DateTime ts, double v, double prv, double pct)>(
            new CommandDefinition(sql, new { thr = thresholdPct }, cancellationToken: ct));
        return rows.Select(x =>
        {
            var sev = x.pct >= 2.0 * thresholdPct ? "critical" : "warning";
            return new AnomalyRow(
                ToMs(x.ts), "return_outlier", sev, column, x.v,
                $"|Δ|={x.pct:F2}% (threshold {thresholdPct}%, prev={x.prv})");
        }).ToList();
    }

    // ── 10. Volume / Turnover inconsistency ───────────────────────────────
    /// <summary>
    /// Flag bars where <c>turnover</c> deviates from
    /// <c>volume × close_price</c> by more than <paramref name="tolerancePct"/>%.
    /// Severity is "warning" — a feed quirk, not a hard violation.
    /// </summary>
    public async Task<IReadOnlyList<AnomalyRow>> DetectVolumeMismatchAsync(
        string tableName, double tolerancePct,
        CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return Array.Empty<AnomalyRow>();
        var tbl  = Safe(tableName);
        var cols = await GetColumnNamesAsync(tbl, ct);
        if (!cols.Contains("volume") || !cols.Contains("turnover") || !cols.Contains("close_price"))
            return Array.Empty<AnomalyRow>();
        if (tolerancePct <= 0) tolerancePct = 5.0;

        await using var conn = await _pg.OpenAsync(ct);
        // Avoid divide-by-zero for tiny expected turnovers (treat as match).
        var sql = $@"
            SELECT timestamp_utc,
                   volume::double precision      AS volume,
                   turnover::double precision    AS turnover,
                   close_price::double precision AS price,
                   (volume::double precision) * (close_price::double precision) AS expected
            FROM ""{tbl}""
            WHERE volume IS NOT NULL AND turnover IS NOT NULL
              AND close_price IS NOT NULL AND volume > 0 AND close_price > 0
              AND ABS((turnover::double precision)
                       - volume::double precision * close_price::double precision)
                  / NULLIF(volume::double precision * close_price::double precision, 0) * 100.0
                  >= @tol
            ORDER BY timestamp_utc";

        var rows = await conn.QueryAsync<(DateTime ts, double volume, double turnover, double price, double expected)>(
            new CommandDefinition(sql, new { tol = tolerancePct }, cancellationToken: ct));
        return rows.Select(x =>
        {
            var dev = x.expected > 0
                ? Math.Abs(x.turnover - x.expected) / x.expected * 100.0
                : 0.0;
            return new AnomalyRow(
                ToMs(x.ts), "volume_turnover_mismatch", "warning", "turnover", x.turnover,
                $"deviation {dev:F2}% (volume×price={x.expected:F2}, tolerance {tolerancePct}%)");
        }).ToList();
    }
}
