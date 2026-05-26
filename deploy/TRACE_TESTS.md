# Service Trace Runner

`deploy/run_service_traces.py` is a console tracer for the deployed ModelLine runtime.
It performs real HTTP request flows and prints a compact colored report by default.

Default output style:

- short step names instead of full URLs
- colored `PASS` / `FAIL` / `SKIP`
- sections grouped by service

For the old debugging format with methods and URLs, use `--verbose-output`.

Covered flows:

- `infra`: `GET /health`, `GET /health/ready`
- `data`: `GET /health`
- `analytics`: `GET /health`, `GET /registry`
- `account`: register, login, me, settings round-trip, refresh, logout
- `gateway`: health, market config, download 100 candles
- `news`: list articles directly or through gateway proxy
- `notification`: unread count, settings read/write round-trip, list notifications
- `social`: list comments, add/remove favorite, create/like/unlike/delete comment
- `admin`: load page and call `/api/health` when an admin head URL is provided

Important contract note:

- the current account API does **not** expose a public delete-account endpoint
- the tracer reports that step as `SKIP unsupported`

## Local full stack

```bash
python3 deploy/run_service_traces.py --insecure-https
```

or:

```bash
bash deploy/run_service_traces.sh --insecure-https
```

## Remote backend host

```bash
python3 deploy/run_service_traces.py \
  --backend-host 95.165.27.159 \
  --insecure-https \
  --only infra,account,gateway,news,notification,social
```

## With separate admin head

```bash
python3 deploy/run_service_traces.py \
  --backend-host 95.165.27.159 \
  --admin-base-url https://sha-trade.tech/admin \
  --insecure-https
```

## JSON report

```bash
python3 deploy/run_service_traces.py --json-report /tmp/modelline-trace-report.json
```

## Verbose debug output

```bash
python3 deploy/run_service_traces.py --verbose-output --color always
```

## Service filtering

```bash
python3 deploy/run_service_traces.py --only account,gateway,social
```

Environment variables are also supported:

- `MODELLINE_TRACER_BACKEND_HOST`
- `MODELLINE_TRACER_INFRA_URL`
- `MODELLINE_TRACER_ADMIN_URL`
- `MODELLINE_TRACER_ACCOUNT_URL`
- `MODELLINE_TRACER_DATA_URL`
- `MODELLINE_TRACER_ANALYTICS_URL`
- `MODELLINE_TRACER_GATEWAY_URL`
- `MODELLINE_TRACER_NEWS_URL`
- `MODELLINE_TRACER_NOTIFICATION_URL`
- `MODELLINE_TRACER_SOCIAL_URL`
- `MODELLINE_TRACER_ONLY`
- `MODELLINE_TRACER_JSON_REPORT`