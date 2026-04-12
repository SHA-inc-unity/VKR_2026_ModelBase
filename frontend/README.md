# frontend

Streamlit unified workspace for:

- one main dashboard-centered workflow
- model fleet monitoring and selected-model drilldown
- market and backtest diagnostics on the same screen
- TXT report export for sharing the current state
- visible reserved run controls without launching the pipeline from the UI

Main screen layout:

- top control bar
- colored summary cards
- model fleet table
- focused model detail area
- market and backtest charts
- fleet diagnostics watchlists
- TXT report preview and export

Execution policy in the frontend:

- refresh is live and clears cached artifact reads
- run buttons are reserved controls only
- no full pipeline execution from the dashboard
- no config editing

Default Streamlit server settings:

- port `8395`
- bind address `0.0.0.0` for non-local access
- configuration source: `.streamlit/config.toml`
