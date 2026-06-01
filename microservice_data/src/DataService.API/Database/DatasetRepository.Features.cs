using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using DataService.API.Dataset;
using Npgsql;
using NpgsqlTypes;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
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
