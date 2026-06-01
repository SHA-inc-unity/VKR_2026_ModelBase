using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using DataService.API.Dataset;
using Npgsql;
using NpgsqlTypes;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
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
        CancellationToken ct = default,
        Action<long, long>? onBatchWritten = null)
    {
        if (rows.Count == 0) return 0;
        var tbl = Safe(tableName);
        var batchSize = DataService.API.Dataset.DatasetConstants.UpsertBatchSize;

        await using var conn = await _pg.OpenAsync(ct);
        long total = 0;

        for (int offset = 0; offset < rows.Count; offset += batchSize)
        {
            // Index directly into the IReadOnlyList — Skip(offset) is O(offset),
            // which made batching O(n²) on large ingests.
            var n = Math.Min(batchSize, rows.Count - offset);

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
                var r = rows[offset + i];
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
            // Report rows processed so far (not affected-row count) so callers
            // can surface real, granular upsert progress instead of one jump.
            onBatchWritten?.Invoke(offset + n, rows.Count);
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
            var n = Math.Min(batchSize, rows.Count - offset);

            var ts     = new DateTime[n];
            var openP  = new decimal?[n];
            var highP  = new decimal?[n];
            var lowP   = new decimal?[n];
            var closeP = new decimal?[n];
            var vol    = new decimal?[n];
            var tv     = new decimal?[n];

            for (int i = 0; i < n; i++)
            {
                var r = rows[offset + i];
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
            var n = Math.Min(batchSize, rows.Count - offset);

            var ts = new DateTime[n];
            var openP = new decimal[n];
            var highP = new decimal[n];
            var lowP = new decimal[n];
            var closeP = new decimal[n];

            for (int i = 0; i < n; i++)
            {
                var row = rows[offset + i];
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
}
