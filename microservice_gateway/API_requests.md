# API Requests For Frontend

Этот файл фиксирует, какие backend/API функции реально нужны Flutter-клиенту по текущему коду в `lib/`.

Цель:

- не терять требования между фронтом и backend;
- разделить уже существующие routes и missing routes;
- явно показать, где frontend сейчас живёт на proxy/fallback логике, а где backend endpoint отсутствует полностью.

## Приоритет P0

### 1. Public market overview for home screen

Проблема:

- главный экран исторически ждал `GET /api/dashboard`;
- новый gateway-contract сделал `/api/dashboard` protected;
- без bearer token пользователь не видел даже overview statistics;
- сейчас во Flutter включён frontend fallback/proxy по публичным market candles, но это именно fallback, а не полноценный backend contract.

Нужен один из вариантов:

1. `GET /api/v1/market/overview`
2. или guest/optional-auth support для `GET /api/dashboard` хотя бы по `marketOverview` и `trendingAssets`

Минимально нужный response shape:

```json
{
  "marketOverview": {
    "totalMarketCap": 0,
    "btcDominance": 0,
    "volume24h": 0,
    "activeAssets": 0,
    "fearGreedValue": 0,
    "fearGreedLabel": "Neutral"
  },
  "trendingAssets": ["BTCUSDT", "ETHUSDT"],
  "meta": {
    "generatedAt": "2026-05-23T00:00:00Z",
    "degradedSections": []
  }
}
```

Почему это нужно:

- home screen должен показывать настоящие market stats, а не только proxy по chart candles;
- fear/greed и total market cap не должны вычисляться на клиенте эвристикой;
- один server-side overview endpoint уменьшит fan-out запросов с клиента.

### 2. Account auth contract usable by Flutter client

Проблема:

- `AuthBloc` сейчас stub-овый;
- login/register во Flutter не интегрированы с live backend;
- часть live account-service инстансов ещё работает на старом email-only login contract.

Нужны endpoints:

1. `POST /api/account/login`
2. `POST /api/account/register`
3. `POST /api/account/refresh`
4. `POST /api/account/logout`
5. `GET /api/account/me`

Минимальные требования:

- login должен принимать `login` или `email` + `password`, а не только email;
- успешный login/register должен возвращать access token, refresh token, `accountType`, `roles`, `email`, `id`;
- refresh/logout должны быть пригодны для mobile/web session flow;
- `GET /api/account/me` уже используется и должен оставаться стабильным.

## Приоритет P1

### 3. Dedicated portfolio API

Сейчас `PortfolioRepository` читает portfolio из `GET /api/dashboard`, но экран портфеля ожидает более богатую структуру.

Предпочтительный route:

- `GET /api/portfolio/summary`

Ожидаемый response shape:

```json
{
  "totalValue": 0,
  "totalPnl": 0,
  "totalPnlPercent": 0,
  "assetCount": 0,
  "exchangeCount": 0,
  "byAsset": [
    {
      "symbol": "BTC",
      "totalAmount": 0,
      "totalValue": 0,
      "change24h": 0,
      "exchangeBreakdown": [
        {
          "exchange": "Binance",
          "amount": 0,
          "value": 0
        }
      ]
    }
  ],
  "byExchange": [
    {
      "exchange": "Binance",
      "totalValue": 0,
      "change24h": 0,
      "isSynced": true,
      "lastSyncedAt": "2026-05-23T00:00:00Z",
      "holdings": [
        {
          "symbol": "BTC",
          "amount": 0,
          "value": 0,
          "change24h": 0
        }
      ]
    }
  ]
}
```

Почему это нужно:

- экран `/portfolio` уже умеет рендерить grouped-by-asset и grouped-by-exchange;
- держать всю эту нагрузку внутри общего `/api/dashboard` неудобно и хуже для partial refresh.

### 4. My Exchanges CRUD

Экран `/my-exchanges` уже есть, но backend contract отсутствует.

Нужны routes:

1. `GET /api/exchanges/available`
2. `GET /api/exchanges/linked`
3. `POST /api/exchanges/link`
4. `PATCH /api/exchanges/link/{slug}`
5. `DELETE /api/exchanges/link/{slug}`

Ожидаемые поля:

- available exchange: `id`, `name`, `slug`, `logoUrl`, `isActive`, `isConnected`
- linked exchange: `name`, `maskedKey`, `cachedBalance`, `linkedAt`

Важно:

- raw API keys не должны возвращаться обратно во frontend;
- нужен masked display value;
- `cachedBalance` полезен для UX на списке linked exchanges.

### 5. Alerts CRUD instead of read-only notifications only

Сейчас экран `/alerts` знает только, что существует `GET /api/notifications`, но этого недостаточно.

Нужны routes:

1. `GET /api/alerts`
2. `POST /api/alerts`
3. `PATCH /api/alerts/{id}`
4. `DELETE /api/alerts/{id}`
5. `GET /api/notifications` оставить для delivery history / inbox

Минимальный alert payload:

```json
{
  "id": "uuid-or-int",
  "symbol": "BTCUSDT",
  "condition": "above",
  "targetPrice": 70000,
  "isEnabled": true,
  "createdAt": "2026-05-23T00:00:00Z"
}
```

## Приоритет P2

### 6. Settings service toggles

В `SettingsScreen` сейчас заглушка: service toggles endpoint не описан.

Нужен route:

- `GET /api/services/toggles`
- опционально `PATCH /api/services/toggles`

Минимальный response:

```json
{
  "news": true,
  "alerts": true,
  "portfolioSync": true,
  "marketOverview": true
}
```

### 7. Admin panel summary surface

`/admin` экран существует, но backend contract для него во Flutter-приложении не описан.

Нужны минимум:

1. `GET /api/admin/summary`
2. `GET /api/admin/users`
3. `GET /api/admin/services`
4. `GET /api/admin/statistics`

Замечание:

- это именно frontend admin needs для `crypt` app;
- не путать с существующим `microservice_admin` backend facade `/api/admin/*` для отдельной admin-панели ModelBase.

## Уже используемые и рабочие routes

Эти routes уже реально используются Flutter-клиентом:

1. `GET /api/app/bootstrap`
2. `GET /api/account/me`
3. `GET /api/dashboard` — protected
4. `GET /api/v1/market/config`
5. `GET /api/v1/market/chart`
6. `GET /api/news`
7. `GET /api/notifications`

## Важные contract notes

### Market chart/config

Frontend сильно опирается на пару:

1. `GET /api/v1/market/config`
2. `GET /api/v1/market/chart`

Требования к стабильности:

- default symbol/timeframe из config не должны возвращать пустые candles без ясной причины;
- `chart` должен продолжать возвращать предсказуемые `status`, `meta.available`, `meta.coverage`;
- symbol names между config и chart должны быть согласованы без ручных alias-хаков на фронте.

### Dashboard degradation

Если backend временно не может вернуть часть sections, для фронта лучше soft-degraded `200` с явным `meta.degradedSections`, чем hard `404`/`500` без структуры.

### Public vs protected split

Если route защищён, желательно иметь один явный public аналог для home/guest experience, а не заставлять Flutter вычислять proxy-метрики на клиенте.