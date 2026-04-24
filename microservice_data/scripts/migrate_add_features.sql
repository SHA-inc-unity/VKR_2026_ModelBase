-- Migration: add 27 feature columns to all existing market-data tables
-- and compute their values via SQL window functions.
-- Idempotent: ADD COLUMN IF NOT EXISTS + full UPDATE (safe to re-run).
-- Generated for: btcusdt_1m, btcusdt_3m, btcusdt_5m, btcusdt_15m,
--                btcusdt_30m, btcusdt_60m, btcusdt_120m, btcusdt_240m,
--                btcusdt_360m, btcusdt_720m, btcusdt_1d

DO $$
DECLARE
    tbl TEXT;
    affected BIGINT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'btcusdt_1m','btcusdt_3m','btcusdt_5m','btcusdt_15m',
        'btcusdt_30m','btcusdt_60m','btcusdt_120m','btcusdt_240m',
        'btcusdt_360m','btcusdt_720m','btcusdt_1d'
    ] LOOP
        RAISE NOTICE 'Processing table: %', tbl;

        -- Step 1: add missing feature columns (idempotent)
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS return_1       double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS return_6       double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS return_24      double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS log_return_1   double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS log_return_6   double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS log_return_24  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll6_mean  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll6_std   double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll6_min   double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll6_max   double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll24_mean double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll24_std  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll24_min  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_roll24_max  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_to_roll6_mean  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_to_roll24_mean double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_vol_6    double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS price_vol_24   double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS oi_roll6_mean  double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS oi_roll24_mean double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS oi_return_1    double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS rsi_lag_1      double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS rsi_lag_2      double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS hour_sin       double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS hour_cos       double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS dow_sin        double precision', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS dow_cos        double precision', tbl);

        RAISE NOTICE '  Columns added/verified for %', tbl;

        -- Step 2: compute all feature values via window functions
        -- Uses two CTE levels: raw_win → rolling intermediates → final UPDATE.
        -- All divisions protected with NULLIF(..., 0).
        EXECUTE format($sql$
            WITH raw_win AS (
                SELECT
                    timestamp_utc,
                    -- returns
                    (index_price::double precision / NULLIF(LAG(index_price::double precision, 1)  OVER w, 0) - 1)::double precision AS return_1,
                    (index_price::double precision / NULLIF(LAG(index_price::double precision, 6)  OVER w, 0) - 1)::double precision AS return_6,
                    (index_price::double precision / NULLIF(LAG(index_price::double precision, 24) OVER w, 0) - 1)::double precision AS return_24,
                    -- log returns
                    LN(GREATEST(index_price::double precision / NULLIF(LAG(index_price::double precision, 1)  OVER w, 0), 1e-10))::double precision AS log_return_1,
                    LN(GREATEST(index_price::double precision / NULLIF(LAG(index_price::double precision, 6)  OVER w, 0), 1e-10))::double precision AS log_return_6,
                    LN(GREATEST(index_price::double precision / NULLIF(LAG(index_price::double precision, 24) OVER w, 0), 1e-10))::double precision AS log_return_24,
                    -- rolling 6
                    AVG(index_price::double precision)       OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 5  PRECEDING AND CURRENT ROW) AS roll6_mean,
                    STDDEV_POP(index_price::double precision) OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 5  PRECEDING AND CURRENT ROW) AS roll6_std,
                    MIN(index_price::double precision)       OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 5  PRECEDING AND CURRENT ROW) AS roll6_min,
                    MAX(index_price::double precision)       OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 5  PRECEDING AND CURRENT ROW) AS roll6_max,
                    -- rolling 24
                    AVG(index_price::double precision)       OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS roll24_mean,
                    STDDEV_POP(index_price::double precision) OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS roll24_std,
                    MIN(index_price::double precision)       OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS roll24_min,
                    MAX(index_price::double precision)       OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS roll24_max,
                    -- OI
                    AVG(open_interest::double precision) OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 5  PRECEDING AND CURRENT ROW) AS oi_roll6,
                    AVG(open_interest::double precision) OVER (PARTITION BY symbol, timeframe ORDER BY timestamp_utc ROWS BETWEEN 23 PRECEDING AND CURRENT ROW) AS oi_roll24,
                    (open_interest::double precision / NULLIF(LAG(open_interest::double precision, 1) OVER w, 0) - 1)::double precision AS oi_return_1,
                    -- RSI lags
                    LAG(rsi::double precision, 1) OVER w AS rsi_lag_1,
                    LAG(rsi::double precision, 2) OVER w AS rsi_lag_2,
                    -- time cyclic
                    SIN(2*PI() * EXTRACT(HOUR FROM timestamp_utc AT TIME ZONE 'UTC') / 24.0)::double precision AS hour_sin,
                    COS(2*PI() * EXTRACT(HOUR FROM timestamp_utc AT TIME ZONE 'UTC') / 24.0)::double precision AS hour_cos,
                    SIN(2*PI() * EXTRACT(DOW  FROM timestamp_utc AT TIME ZONE 'UTC') / 7.0)::double precision  AS dow_sin,
                    COS(2*PI() * EXTRACT(DOW  FROM timestamp_utc AT TIME ZONE 'UTC') / 7.0)::double precision  AS dow_cos
                FROM %I
                WINDOW w AS (PARTITION BY symbol, timeframe ORDER BY timestamp_utc)
            )
            UPDATE %I AS t SET
                return_1            = r.return_1,
                return_6            = r.return_6,
                return_24           = r.return_24,
                log_return_1        = r.log_return_1,
                log_return_6        = r.log_return_6,
                log_return_24       = r.log_return_24,
                price_roll6_mean    = r.roll6_mean,
                price_roll6_std     = r.roll6_std,
                price_roll6_min     = r.roll6_min,
                price_roll6_max     = r.roll6_max,
                price_roll24_mean   = r.roll24_mean,
                price_roll24_std    = r.roll24_std,
                price_roll24_min    = r.roll24_min,
                price_roll24_max    = r.roll24_max,
                price_to_roll6_mean  = (index_price::double precision / NULLIF(r.roll6_mean,  0))::double precision,
                price_to_roll24_mean = (index_price::double precision / NULLIF(r.roll24_mean, 0))::double precision,
                price_vol_6         = (r.roll6_std  / NULLIF(r.roll6_mean,  0))::double precision,
                price_vol_24        = (r.roll24_std / NULLIF(r.roll24_mean, 0))::double precision,
                oi_roll6_mean       = r.oi_roll6,
                oi_roll24_mean      = r.oi_roll24,
                oi_return_1         = r.oi_return_1,
                rsi_lag_1           = r.rsi_lag_1,
                rsi_lag_2           = r.rsi_lag_2,
                hour_sin            = r.hour_sin,
                hour_cos            = r.hour_cos,
                dow_sin             = r.dow_sin,
                dow_cos             = r.dow_cos
            FROM raw_win r
            WHERE t.timestamp_utc = r.timestamp_utc
        $sql$, tbl, tbl);

        GET DIAGNOSTICS affected = ROW_COUNT;
        RAISE NOTICE '  Updated % rows in %', affected, tbl;
    END LOOP;

    RAISE NOTICE 'Migration complete.';
END $$;
