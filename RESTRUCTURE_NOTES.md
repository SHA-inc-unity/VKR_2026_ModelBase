
# Restructured project layout

Core areas:
- `catboost_floader/app` — orchestration entry points
- `catboost_floader/core` — config, utils, logging
- `catboost_floader/data` — ingestion and preprocessing
- `catboost_floader/features` — feature engineering
- `catboost_floader/targets` — target generation
- `catboost_floader/models` — direct, range, confidence, tuning
- `catboost_floader/evaluation` — backtest
- `catboost_floader/monitoring` — anomaly logic

Backward-compatible wrapper modules were kept in the package root to avoid breaking existing imports.
Frontend was also lightly restructured:
- `frontend/pages`
- `frontend/components/charts`
- `frontend/components/cards`
- `frontend/components/tables`
