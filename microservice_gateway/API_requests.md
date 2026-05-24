# API Requests For Frontend

This document describes the backend contracts the current Flutter frontend is now prepared to consume in production. The frontend has been updated to prefer database-backed market snapshots, dedicated home feeds, backend-provided asset icons, and a live converter flow. The remaining work is to make the gateway expose stable production-ready endpoints for those surfaces.

## P0. Required For Current Frontend Behavior

### 1. Database-sorted market snapshot endpoint

Current frontend expectation:

- `GET /api/v1/market/tickers`
- The response must come pre-sorted from the database, not assembled on the client from chart fan-out.
- Supported query params:
  - `page`
  - `pageSize`
  - `search`
  - `sortBy`
  - `sortDir`
  - optional `symbols=BTCUSDT,ETHUSDT,...`
  - optional `collection=market|trending|top-movers`

Required item fields:

- `symbol`
- `displayName`
- `baseAsset`
- `quoteAsset`
- `price`
- `change24h`
- `volume24h`
- `marketCap`
- `high24h`
- `low24h`
- `rank`
- `logoUrl`
- `exchangeCount`
- `updatedAt`

Why this matters:

- The markets page must load already sorted from backend storage.
- The frontend should not fetch hundreds of chart payloads just to render a sortable list.
- The same snapshot contract is also now used for converter options, asset metadata, and home cards.

### 2. Dedicated backend feeds for home trending and top movers

Current frontend expectation:

- `GET /api/v1/market/trending?limit=5`
- `GET /api/v1/market/top-movers?limit=5`

Required behavior:

- `trending` must be an explicitly curated backend feed, not a UI-side fallback from overview.
- `top-movers` must be pre-ranked server-side by current 24h move, ideally by absolute move unless product decides otherwise.
- Both endpoints should return the same item contract as `GET /api/v1/market/tickers`.

Why this matters:

- The home screen now loads `Trending` and `Top movers` separately from backend-ready repository methods.
- These sections should stop depending on overview symbols or chart polling fallbacks.

### 3. Stable asset metadata for icons and detail screens

Current frontend expectation:

- `logoUrl`, `displayName`, and `marketCap` must be available from the same market snapshot family.
- The frontend now looks up single-asset metadata via `GET /api/v1/market/tickers?symbols=...` when it needs icons and capitalization outside the market list.

Required behavior:

- `logoUrl` should be backend-owned and stable for:
  - market list items
  - trending items
  - top movers
  - single-symbol metadata lookups
- `displayName` should be human-readable and not require frontend symbol heuristics.
- `marketCap` must be real numeric data or `null`, never a placeholder string.

Why this matters:

- Currency icons should load from backend data.
- Asset detail should stop showing broken market-cap placeholders.

### 4. Real capitalization and overview metrics

Current frontend expectation:

- `GET /api/v1/market/overview`

Required fields:

- `totalMarketCap`
- `volume24h`
- `btcDominance`
- `fearGreedValue`
- `fearGreedLabel`
- `activeAssets`
- `generatedAt` or `updatedAt`
- optional `degradedSections`

Required behavior:

- Return real aggregated values in production.
- If some metrics are unavailable, return `null` plus explicit degradation metadata.
- Do not return UI-breaking placeholder values like `0` for everything or textual placeholders such as `404 API`.

Why this matters:

- Home overview and asset detail currently rely on these values to present capitalization and market health.

### 5. News feed contract for both home and full news screen

Current frontend expectation:

- `GET /api/news`
- Optional dedicated teaser route: `GET /api/news/home?limit=3`

Required query support:

- `limit`
- optional `tag`
- optional pagination or cursor field if the feed grows

Required item fields:

- `id`
- `title`
- `summary`
- `source`
- `publishedAt`
- `url`
- optional `imageUrl`
- optional `tags`

Required behavior:

- Results must be newest-first and stable.
- Home teaser and full news screen must read from backend, not from a client-side mock.
- If `dashboard.latestNews` remains available as a fallback, the dedicated news feed should still be treated as the primary contract.

Why this matters:

- The news screen and home news section have been prepared to render real backend stories, but they need a reliable feed contract.

### 6. Converter quote endpoint

Current frontend expectation:

- `GET /api/v1/market/convert`

Required query params:

- `from`
- `to`
- `amount`

Required response fields:

- `from`
- `to`
- `amount`
- `rate`
- `convertedAmount`
- `sourceLabel`
- `updatedAt`

Why this matters:

- The converter screen is no longer a placeholder on the frontend side.
- The current temporary client-side conversion should be replaced by a backend-owned quote contract.

### 7. Cheap 5-second market refresh contract

Current frontend expectation:

- The markets screen now performs a 5-second visual price pulse on the client.
- It still needs cheap backend snapshots to rebase those values regularly.

Required backend behavior:

- `GET /api/v1/market/tickers` should be cheap enough to poll frequently.
- Each item should carry `updatedAt`.
- The payload should ideally also include a top-level `snapshotId` or `sequence` so the frontend can detect real server updates.
- Expose sensible `Cache-Control` and/or `ETag` semantics for public market snapshot routes.

Why this matters:

- The frontend should not use chart endpoints as a pseudo-realtime list transport.

## P1. Protected And Auth-sensitive Routes

### 8. Stable auth envelope for alerts

Current frontend expectation:

- Guest users no longer see alerts entry points in the UI.
- Direct route hits and stale sessions can still happen.

Required behavior:

- `/api/alerts/*` should always return one stable JSON error envelope on auth failure.
- Expected fields:
  - `status`
  - `title`
  - `detail`
  - optional `code`

Why this matters:

- Even though alerts are hidden for guests, direct navigation and token expiry still need predictable backend behavior.

## P2. Compatibility Notes

### 9. Keep old chart and overview routes valid while new snapshot endpoints land

Current frontend expectation:

- The frontend still contains compatibility fallbacks for:
  - `GET /api/v1/market/config`
  - `GET /api/v1/market/chart`
  - `GET /api/v1/market/overview`

Required behavior:

- Keep those routes stable while the richer snapshot and home-feed endpoints are being added.
- Once `tickers`, `trending`, `top-movers`, `convert`, and stable `news` are live, the remaining chart fan-out code can be removed from the frontend.

Why this matters:

- This lets backend and frontend migrate incrementally without another placeholder cycle on production screens.

## P1. Protected And Auth-sensitive Routes

### 8. Stable auth envelope for alerts

Current frontend expectation:

- Guest users no longer see alerts entry points in the UI.
- Direct route hits and stale sessions can still happen.

Required behavior:

- `/api/alerts/*` should always return one stable JSON error envelope on auth failure.
- Expected fields:
  - `status`
  - `title`
  - `detail`
  - optional `code`

Why this matters:

- Even though alerts are hidden for guests, direct navigation and token expiry still need predictable backend behavior.

## P2. Compatibility Notes

### 9. Keep old chart and overview routes valid while new snapshot endpoints land

Current frontend expectation:

- The frontend still contains compatibility fallbacks for:
  - `GET /api/v1/market/config`
  - `GET /api/v1/market/chart`
  - `GET /api/v1/market/overview`

Required behavior:

- Keep those routes stable while the richer snapshot and home-feed endpoints are being added.
- Once `tickers`, `trending`, `top-movers`, `convert`, and stable `news` are live, the remaining chart fan-out code can be removed from the frontend.

Why this matters:

- This lets backend and frontend migrate incrementally without another placeholder cycle on production screens.