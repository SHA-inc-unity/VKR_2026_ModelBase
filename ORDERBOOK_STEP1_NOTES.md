
# Order Book Step 1

This change adds the first roadmap item only: Order Flow / Order Book structure and base features.

## New files
- `catboost_floader/data/orderbook.py`
- `catboost_floader/features/orderbook_features.py`

## What changed
- `data_ingestion.py` now delegates ticker/orderbook snapshot parsing to `data/orderbook.py`
- `feature_engineering.py` now delegates order book feature construction to `features/orderbook_features.py`

## Scope
- no target changes
- no model changes
- no anomaly changes
- no UI changes

## Added features
- `top_spread`
- `spread_zscore`
- `top_imbalance`
- `imbalance_delta_1`
- `imbalance_delta_5`
- `microprice`
- `depth_bid`
- `depth_ask`
- `depth_imbalance`
