# Prompt: Fix public market chart pending/fatal-error defect

Нужно исправить дефект свечного графика в связке gateway + Flutter client. Симптом на production: chart screen часто открывается с фатальной ошибкой вместо свечей.

## Что уже подтверждено

### 1. Live gateway contract из браузерного контекста
- `GET /api/v1/market/config` отвечает `200` и возвращает:
  - `defaults.timeframe = "5m"`
  - `defaults.candleCount = 200`
  - heavy candle grid: `[50, 100, 200, 500]`
  - `OGUSDT` присутствует в `symbols`
- `GET /api/v1/market/chart?symbol=BTCUSDT&timeframe=1m&limit=200` отвечает `200` с телом:

```json
{"symbol":"BTCUSDT","timeframe":"1m","limit":200,"candles":[],"meta":{"requested":200,"available":0,"fromMs":0,"toMs":0,"coverage":"pending"},"status":"pending","retryAfterMs":5000}
```

- `GET /api/v1/market/chart?symbol=OGUSDT&timeframe=1m&limit=200` отвечает аналогично: `200 status=pending`, `candles=[]`.

Важно: обычный `curl` с сервера упирается в Cloudflare challenge и не отражает реальное поведение браузерного клиента. Проверка выше сделана через browser `fetch(...)` в уже открытом production page context.

### 2. Frontend сам превращает `pending` в фатальный `503`

Файлы:
- `/home/ubuntu/VKR_2026_crypt/lib/data/repositories/candle_repository.dart`
- `/home/ubuntu/VKR_2026_crypt/lib/presentation/blocs/asset_detail/asset_detail_bloc.dart`
- `/home/ubuntu/VKR_2026_crypt/lib/presentation/screens/asset/asset_chart_screen.dart`
- `/home/ubuntu/VKR_2026_crypt/lib/presentation/screens/asset/asset_detail_screen.dart`
- `/home/ubuntu/VKR_2026_crypt/lib/presentation/blocs/asset_detail/asset_detail_event.dart`

Текущая цепочка:
- `LoadAssetDetail` по умолчанию использует `timeframe = '1m'`.
- `AssetChartScreen` тоже стартует с `initialTimeframe = '1m'`.
- `AssetDetailScreen` создаёт `AssetDetailBloc()..add(LoadAssetDetail(symbol))`, то есть тоже падает в `1m` по умолчанию.
- `CandleRepository.getCandles()` делает до 3 retry для `status == 'pending'`.
- Если после этих попыток свечей всё ещё нет, репозиторий сам бросает:
  - `ApiException(statusCode: 503, code: 'market_chart_pending', detail: 'GET /api/v1/market/chart is still preparing candles ...')`
- `AssetDetailBloc` превращает это в `AssetDetailError(apiErrorMessage(error))`.
- `AssetChartScreen` и `AssetDetailScreen` рисуют `ApiErrorWidget.notFound(...)`, то есть transient `pending` в итоге показывается пользователю как фатальный экран.

Итог: даже когда gateway честно отвечает `200 pending`, Flutter UI сам эскалирует это в fatal-error UX.

### 3. Backend code сейчас противоречит ожидаемому контракту и истории изменений

Файлы:
- `/home/ubuntu/VKR_2026_ModelBase/microservice_gateway/src/GatewayService.API/Market/ChartService.cs`
- `/home/ubuntu/VKR_2026_ModelBase/microservice_gateway/src/GatewayService.API/Market/IDataServiceClient.cs`
- `/home/ubuntu/VKR_2026_ModelBase/microservice_gateway/src/GatewayService.API/Market/DataServiceClient.cs`
- `/home/ubuntu/VKR_2026_ModelBase/microservice_gateway/src/GatewayService.API/Controllers/MarketController.cs`
- `/home/ubuntu/VKR_2026_ModelBase/microservice_gateway/tests/GatewayService.UnitTests/Market/ChartServiceTests.cs`
- `/home/ubuntu/VKR_2026_ModelBase/microservice_gateway/tests/GatewayService.UnitTests/MarketControllerTests.cs`

Критичный факт по текущему коду:
- В `ChartService.GetChartAsync(...)`, если `latestRows.IsEmpty`, сервис вызывает `TryTriggerWindowHydrationAsync(...)`.
- Эта ветка уходит в `TriggerIngestInBackground(...)`.
- Там используется только `_data.FireAndForgetIngest(...)`.
- После этого сервис сразу возвращает `BuildPendingResponse(...)`.

То есть public chart-path на cold miss сейчас **не пытается синхронно догрузить окно и перечитать rows в рамках текущего HTTP-запроса**.

При этом:
- `IDataServiceClient.IngestAsync(...)` существует.
- `DataServiceClient.IngestAsync(...)` реализован.
- Исторические notes/docs в этом репозитории утверждают, что chart-path должен либо пытаться synchronously hydrate missing window, либо как минимум не зависать в ложном вечном `pending`.
- Unit test `No_latest_window_data_triggers_sync_ingest_and_returns_ok_when_rows_arrive` в `ChartServiceTests.cs` явно ожидает другой контракт: empty latest rows -> ingest -> reread rows -> `status == "ok"`.

То есть сейчас очень похоже на backend regression: активный код уже не соответствует ни ожиданиям теста, ни задокументированному поведению.

## Что нужно исправить

### Backend: вернуть корректное поведение chart cold-miss path

Исправить root cause в gateway, а не маскировать только на клиенте.

Требования:
- В `ChartService.GetChartAsync(...)` на ветке `latestRows.IsEmpty` не ограничиваться background-only `FireAndForgetIngest`.
- Для валидного `(symbol, timeframe, limit)` пытаться bounded synchronous lazy hydrate missing window через `IDataServiceClient.IngestAsync(...)`.
- После успешного ingest повторно читать rows и стараться вернуть `ChartResponse` в том же HTTP-ответе.
- `pending` оставлять только для реально transient сценариев:
  - ingest lock уже занят другим запросом
  - claim-check / слишком большой payload
  - bounded wait budget исчерпан, но ingest реально в процессе
- Явные downstream failures/timeouts/busy продолжать мапить в `503` через текущую логику `MarketController`:
  - `DATA_SOURCE_UNAVAILABLE`
  - `DOWNSTREAM_TIMEOUT`
  - `SERVICE_BUSY`
- Не ломать существующие cache semantics, `ETag`, `Last-Modified`, `Cache-Control`, `ChartRequestQueue` и status mapping.

Желательный ориентир по реализации:
- Если локальный cache miss + `latestRows.IsEmpty` + ingest lock свободен:
  1. acquire ingest lock
  2. вызвать `_data.IngestAsync(...)` c bounded timeout/budget
  3. если ingest success -> `_data.GetRowsAsync(...)` и вернуть `ok`/`partial`
  4. если ingest timeout/busy/failure -> вернуть `pending` или `503` строго по типу результата, а не всё подряд как empty pending
  5. корректно очистить или перевести ingest lock в cooldown

### Frontend: перестать делать fatal screen из retryable chart state

Даже после backend fix UI должен быть устойчивым к transient `pending`.

Требования:
- Не использовать жёсткий дефолт `1m` для asset chart/detail экранов.
- Взять initial timeframe из gateway config:
  - приоритетно `config.defaults.timeframe`
  - либо первый допустимый supported timeframe, если defaults недоступен
- Исправить это как минимум в:
  - `LoadAssetDetail` default
  - `AssetChartScreen.initialTimeframe`
  - первичную загрузку `AssetDetailScreen`
- Не превращать `status == 'pending'` в фатальный `AssetDetailError`.
- Для `market_chart_pending` и retryable `503` показывать pending/loading state:
  - спиннер или skeleton
  - сообщение, что свечи готовятся
  - авто-retry с backoff
  - если уже есть предыдущие свечи, не стирать их, а оставлять stale chart + badge `updating`
- `ApiErrorWidget` должен оставаться только для реальных terminal errors, а не для normal pending lifecycle.

## Что проверить после исправления

### Backend acceptance
- `GET /api/v1/market/chart?symbol=BTCUSDT&timeframe=1m&limit=200` на cold cache больше не застревает в бесконечном `200 pending` без свечей при нормальной доступности data-service.
- Первый hit должен по возможности вернуть `ok` или `partial` в том же запросе.
- `pending` должен появляться только в реально transient сценариях.
- `503` должен оставаться только для реальных service unavailable cases.

### Frontend acceptance
- При открытии `/asset/:symbol` и `/asset/:symbol/chart` пользователь не попадает сразу в фатальный error screen только потому, что `1m` window ещё не готова.
- Если gateway временно отвечает `pending`, UI показывает retryable loading state, а не fatal error.
- Если gateway config default timeframe = `5m`, клиент стартует именно с него, а не с hardcoded `1m`.

### Tests / validation
- Backend:
  - прогнать и при необходимости починить `ChartServiceTests`
  - прогнать `MarketControllerTests`
  - добавить/обновить tests для cold miss -> sync ingest -> rows reread -> `ok`
  - добавить test, что `pending` остаётся только для ingest-lock / claim-check / bounded transient cases
- Frontend:
  - минимум `flutter analyze`
  - `flutter build web`
  - по возможности bloc-level test на pending handling

## Ограничения и замечания

- Не считать `curl` с backend host источником истины для production endpoint: там Cloudflare challenge.
- Источником истины для live behavior здесь является browser fetch из уже открытого приложения.
- Исправление должно быть root-cause oriented: backend cold-miss path + frontend pending UX, а не только cosmetic retry.
- Не ломать существующую public chart contract validation по `limit` и `timeframe grid`.

## Короткий диагноз в одну фразу

Сейчас chart ломается из-за комбинации двух дефектов: gateway на cold miss возвращает бесконечный `200 pending` с пустыми свечами вместо bounded synchronous hydrate или чёткого terminal результата, а Flutter client по hardcoded `1m` default и after-retry logic превращает этот transient `pending` в фатальный `503` экран.