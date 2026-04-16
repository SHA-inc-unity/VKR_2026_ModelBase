# Agent Instructions — VKR_2026_ModelBase

Cryptocurrency market dataset pipeline + Streamlit dashboard.
Data source: Bybit v5 REST API → PostgreSQL (`crypt_date` db).
UI: Streamlit multi-page app (`frontend/`).

---

## Architecture

```
frontend/app.py               Landing page (switch_page to download_page)
frontend/pages/download_page.py  Full dataset UI: coverage check, download, visualize
frontend/services/charts.py   Plotly chart builders (FIELD_META + CHART_GROUPS pattern)
frontend/services/db_auth.py  DB config: env vars → .db_config.json → defaults
backend/dataset/              Backend package: API, schema, features, pipeline
build_market_dataset_to_postgres.py  Compatibility CLI wrapper → backend.dataset.main()
```

`download_page.py` imports `backend.dataset` directly via `sys.path` manipulation. The root `build_market_dataset_to_postgres.py` file remains only as a thin compatibility wrapper for CLI and older imports.

---

## Run Commands

```bash
# Streamlit UI (port 8395, configured in .streamlit/config.toml)
streamlit run frontend/app.py

# Backend CLI (direct data fetch)
python build_market_dataset_to_postgres.py \
  --symbol BTCUSDT --start 2024-01-01T00:00:00Z --end 2024-12-31T23:59:59Z \
  --postgres-user postgres --postgres-password postgres

# DB connection via env vars
export PGHOST=localhost PGPORT=5432 PGDATABASE=crypt_date PGUSER=postgres PGPASSWORD=postgres
```

Python interpreter: `.venv/bin/python` (see `.vscode/settings.json`).

---

## Database

- **DB name:** `crypt_date` · host `localhost:5432`
- **Table name pattern:** `{symbol_lower}_{timeframe_lower}` (e.g. `btcusdt_60m`)
- **Schema:** one main table per symbol/timeframe with raw market columns plus feature columns and `target_return_1`; no separate `_features` table
- **Upsert:** `INSERT … ON CONFLICT (timestamp_utc) DO UPDATE … RETURNING (xmax = 0) AS inserted`
- **Config precedence:** Manual UI override → env vars (`PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD`) → `.db_config.json` → hardcoded defaults

---

## Key Conventions

- **Language:** Russian comments and docstrings throughout — do not switch to English.
- **Timestamps:** Always milliseconds (UTC) in API calls; `TIMESTAMPTZ` in Postgres; ISO8601 in UI.
- **RSI:** Wilder's smoothing (not SMA-seeded); recomputed over window + lookback on every download to avoid warm-up bias. Bounds `[0, 100]` are validated after recomputation.
- **Timeframes supported:** `1m 3m 5m 15m 30m 60m 120m 240m 360m 720m 1d`
- **Session state reset:** `controls_signature` (hash of all inputs) — reset `coverage`, `dataset`, `last_download` when it changes.
- **Streamlit pages:** Uses `st.switch_page()` for navigation; page files live under `frontend/pages/`.

---

## Known Pitfalls

- **Bid/ask columns are always NULL** — Bybit v5 public history doesn't include L1 order book data.
- **PyArrow / `st.dataframe` crash** when a column mixes bool/float/str — stringify such columns before rendering.
- **RSI two-stage compounding bug:** deriving the soft-limit from already-shrunk deviation amplifies flattening; use pre-shrink deviation as the reference.
- **60m family stabilization:** needs softer shrink, larger soft-limit multiplier, more expectation weight vs. other timeframes.
- **Missing deps cause silent failures:** `scikit-learn`, `catboost`, `tqdm` are required by backend jobs; absence causes subprocess exit code 1 while the UI shows "running".
- **No requirements.txt** — dependencies inferred from imports: `psycopg2-binary`, `streamlit`, `pandas`, `plotly`.

---

## Charts Pattern

`charts.py` uses two dicts:
- `FIELD_META`: maps field name → `{label, color}` for every plottable column
- `CHART_GROUPS`: ordered list of logical groups, each with field list + rendering hints (e.g. dual-axis for `market_metrics`, hlines for `indicators`)

To add a new chart field: add an entry to `FIELD_META`, add it to the relevant `CHART_GROUPS` entry (or create a new group), then add a builder function and wire it into `render_charts()`.
