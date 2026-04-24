-- Migration 003: Add OHLCV raw columns to all market-data tables.
-- Idempotent: ADD COLUMN IF NOT EXISTS.
-- Feature columns derived from OHLCV (atr_6, atr_24, candle_body, upper_wick,
-- lower_wick, volume_roll*_mean, volume_to_roll*_mean, volume_return_1,
-- rsi_slope) are added automatically by DatasetRepository.ComputeAndUpdateFeaturesAsync
-- (also via ADD COLUMN IF NOT EXISTS) when a compute_features job is run.
-- Generated for: btcusdt_1m, btcusdt_3m, btcusdt_5m, btcusdt_15m,
--                btcusdt_30m, btcusdt_60m, btcusdt_120m, btcusdt_240m,
--                btcusdt_360m, btcusdt_720m, btcusdt_1d

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'btcusdt_1m', 'btcusdt_3m', 'btcusdt_5m', 'btcusdt_15m',
        'btcusdt_30m', 'btcusdt_60m', 'btcusdt_120m', 'btcusdt_240m',
        'btcusdt_360m', 'btcusdt_720m', 'btcusdt_1d'
    ]
    LOOP
        RAISE NOTICE 'Migration 003 — adding OHLCV columns to table: %', tbl;
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS open_price  NUMERIC', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS high_price  NUMERIC', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS low_price   NUMERIC', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS volume      NUMERIC', tbl);
        EXECUTE format('ALTER TABLE %I ADD COLUMN IF NOT EXISTS turnover    NUMERIC', tbl);
        RAISE NOTICE 'Migration 003 — done: %', tbl;
    END LOOP;
END $$;
