# Exchange API Notes

Краткая памятка по биржевым API, которые реально использует ModelLine в dataset pipeline.

## Источники

- Kraken API Center: https://docs.kraken.com/api/
- Kraken OHLC: https://docs.kraken.com/api/docs/rest-api/get-ohlc-data/
- Kraken Asset Pairs: https://docs.kraken.com/api/docs/rest-api/get-tradable-asset-pairs/
- Kraken REST rate limits: https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits/
- Bybit developer landing: https://www.bybit.com/en/derivative-activity/developer/
- Bybit Kline: https://bybit-exchange.github.io/docs/v5/market/kline
- Bybit Funding Rate History: https://bybit-exchange.github.io/docs/v5/market/history-fund-rate
- Bybit Open Interest: https://bybit-exchange.github.io/docs/v5/market/open-interest
- Bybit Rate Limits: https://bybit-exchange.github.io/docs/v5/rate-limit
- Binance landing page: https://www.binance.com/en/binance-api
- Binance futures docs used by the service:
  - https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information
  - https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data
  - https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
  - https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics

Примечание по Binance: landing page на `binance.com/en/binance-api` в headless fetch-среде часто упирается в JS/WAF-check. Для endpoint-level логики удобнее сразу читать canonical docs на `developers.binance.com`.

## Быстрое сравнение

| Exchange | Klines | Funding | Open Interest | Symbol metadata | Главные ограничения |
| --- | --- | --- | --- | --- | --- |
| Kraken | Да, spot OHLC | Нет в текущем pipeline | Нет в текущем pipeline | `GET /0/public/AssetPairs` | только последние 720 base candles, публичный REST rate counter |
| Bybit | Да | Да | Да | market/instrument endpoints | 600 req / 5 s / IP, endpoint-level UID limits |
| Binance | Да | Да | Да | `GET /fapi/v1/exchangeInfo` | kline weight зависит от `limit`, funding 500 / 5 min / IP, OI history только за 1 месяц |

## Kraken

### Что используем

- `GET /0/public/OHLC`
- `GET /0/public/AssetPairs`

### Что важно

- OHLC endpoint всегда возвращает не только закрытые бары, но и последний текущий, ещё не зафиксированный бар.
- Независимо от `since`, Kraken отдаёт максимум 720 последних записей для выбранного upstream interval.
- Допустимые интервалы OHLC: `1, 5, 15, 30, 60, 240, 1440, 10080, 21600` минут.
- AssetPairs endpoint поддерживает query parameter `pair`, поэтому для одного символа нельзя тащить весь каталог. Для ModelLine правильный путь: узкий lookup по одному candidate за раз (`BTCUSDT`, затем `BTC/USDT`, затем `XBTUSDT`, ...), потому что Kraken может вернуть `EQuery:Unknown asset pair`, если смешать валидные и невалидные candidates в одном запросе.
- Публичный REST имеет общий call counter. При перегрузе возможны ошибки `EAPI:Rate limit exceeded` и `EService: Throttled: <timestamp>`.
- На практике public OHLC path для ModelLine также возвращал `EGeneral:Too many requests`, если несколько Kraken ingest jobs одновременно запускали paged window-fetch без локального throttling.

### Что можно

- Получать spot OHLC для поддерживаемых пар.
- Агрегировать неподдерживаемые higher-level TF локально из меньших upstream bars.
- Делать точечный symbol resolution через `pair=` фильтр.

### Что нельзя или не стоит делать

- Нельзя рассчитывать на старую историю глубже reachable window: upstream older candles просто недоступны.
- Нельзя ожидать funding rate и open interest из текущего public spot path.
- Нельзя использовать полный `AssetPairs` catalog как hot path для каждой ingest job.

### Как ModelLine трактует Kraken

- `kraken` в data-service = OHLC-only pipeline.
- Funding и open interest остаются `NULL`.
- Requested window сначала клипуется к reachable lookback.
- Если окно полностью вне reachable history, ingest завершается как no-op, а не как runtime failure.
- Pair resolution идёт через filtered `AssetPairs?pair=...` candidate-by-candidate, чтобы не зависать на гигантском каталоге и не ловить mixed-candidate `EQuery:Unknown asset pair`.
- Scheduler может держать до 4 Kraken ingest jobs одновременно, но сам HTTP client теперь сериализует/разрежает реальные REST calls process-local limiter-ом и ретраит Kraken throttle-ответы вместо мгновенного job failure.

## Bybit

### Что используем

- `GET /v5/market/kline`
- `GET /v5/market/funding/history`
- `GET /v5/market/open-interest`

### Что важно

- `kline` принимает `category=spot|linear|inverse`, по умолчанию `linear`.
- Символы должны быть uppercase, например `BTCUSDT`.
- `kline.limit`: `1..1000`, ответ идёт в reverse order по `startTime`.
- Funding history покрывает `linear` и `inverse`; `startTime` без `endTime` возвращает ошибку.
- Open interest покрывает `linear` и `inverse`; query не уходит раньше launch time symbol-а, а при экстремальной волатильности API может замедляться.
- HTTP IP limit: 600 requests / 5 seconds / IP.
- API также отдаёт endpoint-level лимиты в response headers: `X-Bapi-Limit`, `X-Bapi-Limit-Status`, `X-Bapi-Limit-Reset-Timestamp`.

### Что можно

- Полный perpetual pipeline: свечи, funding, open interest.
- Нормально window-ить historical fetch с paginated REST.

### Что нельзя или не стоит делать

- Нельзя держать concurrency на грани IP limit.
- Нельзя отправлять `startTime` без `endTime` в funding history.
- Нельзя рассчитывать, что open-interest endpoint всегда будет одинаково быстрым во время рыночных всплесков.

### Как ModelLine трактует Bybit

- Bybit остаётся baseline exchange: полный pipeline `klines + funding + OI`.
- Для тяжёлых TF параллелизм режется сильнее, чтобы не выжигать общий rate budget.

## Binance

### Что используем

- `GET /fapi/v1/exchangeInfo`
- `GET /fapi/v1/klines`
- `GET /fapi/v1/fundingRate`
- `GET /futures/data/openInterestHist`

### Что важно

- `exchangeInfo` даёт symbol metadata и rate limits; в ответе обычно есть `REQUEST_WEIGHT=2400 / min` и `ORDERS=1200 / min`.
- `klines.limit`: default 500, max 1500.
- Request weight для klines зависит от `limit`.
- Funding history делит лимит `500 / 5 min / IP` с `GET /fapi/v1/fundingInfo`.
- Funding history возвращается в ascending order; если диапазон слишком большой, сервер отрежет ответ по `startTime + limit`.
- `openInterestHist` хранит только последний 1 месяц истории, max `limit=500`, IP rate limit `1000 requests / 5 min`.

### Что можно

- Полный futures pipeline: candles, funding, open interest.
- Получать symbol rules и limits из `exchangeInfo` перед запуском новых интеграций.

### Что нельзя или не стоит делать

- Нельзя игнорировать request-weight scaling у klines.
- Нельзя ожидать open-interest историю старше 1 месяца.
- Нельзя опираться только на JS landing page как на машиночитаемый source of truth.

### Как ModelLine трактует Binance

- Binance работает как full futures pipeline.
- Для больших окон важно помнить, что kline weight растёт вместе с `limit`, а OI-history физически обрезана последним месяцем.

## Правило для ModelLine

- Bybit и Binance считаются full-feed биржами: `klines + funding + open interest`.
- Kraken считается OHLC-only биржей.
- Если новая фича требует funding/OI для Kraken, текущий public path этого не даёт и нужно проектировать отдельный источник данных, а не пытаться «дожать» текущий client.