# Exchange API Notes

Краткая памятка по биржевым API, которые реально использует ModelLine в dataset pipeline.

## Источники

- JKorf Binance.Net: https://github.com/JKorf/Binance.Net
- JKorf Bybit.Net: https://github.com/JKorf/Bybit.Net
- JKorf Kraken.Net: https://github.com/JKorf/Kraken.Net
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

## Realtime layer in ModelLine

Исторический pipeline по-прежнему идёт через внутренние REST clients репозитория.
Но perpetual `market_watch` worker не делает REST polling свечей по всем
`exchange × symbol × timeframe` каждую секунду: это не укладывается в реальный
budget API, особенно для Kraken.

Для realtime prices worker использует поддерживаемые .NET websocket clients:

- `Binance.Net` — targeted USD-M book-ticker stream по whitelist symbols
- `Bybit.Net` — V5 linear depth-1 orderbook subscriptions
- `KrakenExchange.Net` — managed spot order books по whitelist symbols

Watcher считает live-price по best bid/ask midpoint там, где биржа даёт более
частые book/orderbook updates, чем last-trade ticker. В `market_watch_live`
сохраняется last closed candle per timeframe плюс текущая live price row.
Одновременно на каждом candle rollover watcher теперь зеркалит закрытую
O/H/L/C свечу в canonical dataset table для этого `exchange + symbol + timeframe`,
но не трогает volume/turnover/funding/OI/RSI и derived features. Это live
bridge для свежести raw tables, а не замена ingest/repair/upsert как
authoritative historical source.

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
- Если клипованное окно помещается в reachable history, client сначала пробует один `/0/public/OHLC` на весь диапазон и только при неполном покрытии откатывается к более мелким throttled pages.
- Если окно полностью вне reachable history, ingest завершается как no-op, а не как runtime failure.
- Pair resolution идёт через filtered `AssetPairs?pair=...` candidate-by-candidate, чтобы не зависать на гигантском каталоге и не ловить mixed-candidate `EQuery:Unknown asset pair`.
- Scheduler может держать до 4 Kraken ingest jobs одновременно, но сам HTTP client теперь сериализует/разрежает реальные REST calls process-local limiter-ом и ретраит Kraken throttle-ответы вместо мгновенного job failure.
- Для realtime worker Kraken больше не входит в default `DataService:MarketWatch:Exchanges`: live runtime по умолчанию работает только на `bybit` + `binance`, а historical ingest по Kraken остаётся без изменений. Kraken live path оставлен как manual opt-in.
- Если Kraken явно re-enable-нут для realtime worker, он по-прежнему ограничен `*USDT` spot universe. При этом наружу worker держит канонический dataset symbol (`BTCUSDT`-style), а startup discovery больше не зависит от полного `AssetPairs` каталога: watcher адресно резолвит только whitelist `BASE/USDT` pairs и использует этот canonical alias для websocket subscription. Это устраняет runtime defect, при котором медленный каталог или Kraken metadata вроде `XBTUSDT`/`XBT/USDT` выбрасывали биржу из live watcher-а после рестарта.
- Live price для opt-in Kraken path идёт не через sparse ticker/last-trade signal, а через managed spot order books. Watcher публикует midpoint best bid/ask по synced book и дополнительно переиздаёт текущий top-of-book коротким runtime refresh-loop'ом.
- Если opt-in Kraken websocket startup ловит `RateLimitRequest` / HTTP `429`, watcher не роняет уже живые Binance/Bybit subscriptions. Он продолжает работать в `running` с degraded message, пишет причину в watcher logs/status и сразу вычищает stale Kraken rows из runtime state и `market_watch_live`, чтобы UI не показывал ложную live-биржу с большим лагом.

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
- Heavy `1m`/`3m` jobs по-прежнему сериализуются scheduler-ом на уровне биржи, но внутри одного Bybit job ModelLine теперь держит более широкий kline fan-out `6`; общий IP budget всё равно ограничивается shared token bucket, поэтому ускорение long backfills не требует ослаблять safety-лимит.
- Для realtime worker Bybit идёт через websocket depth-1 orderbook (`Bybit.Net`) вместо 1s REST polling по каждому symbol/timeframe; live price считается как midpoint best bid/ask, потому что этот stream заметно свежее для thin symbols, чем last-trade ticker.

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
- `openInterestHist` хранит только последние 30 дней истории, max `limit=500`, IP rate limit `1000 requests / 5 min`.

### Что можно

- Полный futures pipeline: candles, funding, open interest.
- Получать symbol rules и limits из `exchangeInfo` перед запуском новых интеграций.

### Что нельзя или не стоит делать

- Нельзя игнорировать request-weight scaling у klines.
- Нельзя ожидать open-interest историю старше 1 месяца.
- Нельзя опираться только на JS landing page как на машиночитаемый source of truth.

### Как ModelLine трактует Binance

- Binance работает как full futures pipeline.
- Для больших окон важно помнить, что kline weight растёт вместе с `limit`, а OI-history физически обрезана последними 30 днями.
- В ModelLine безопасная трактовка `openInterestHist` — строгие 30 дней с подъёмом `startTime` до ближайшей допустимой границы периода; иначе Binance отвечает `400 parameter 'startTime' is invalid`.
- Heavy `1m`/`3m` jobs у ModelLine по-прежнему сериализуются на уровне scheduler, поэтому внутри одного Binance job можно держать более широкий page fan-out, пока process-local limiter остаётся ниже общего REST weight budget.
- Текущий Binance limiter в repo настроен заметно быстрее прежнего conservative режима: примерно `1.7k` weight/min shared budget вместо старых `~600/min`, что даёт кратный выигрыш на million-candle backfills без отказа от `Retry-After`/penalty semantics.
- Для realtime worker Binance использует targeted websocket book-ticker stream (`Binance.Net`) только по whitelist symbols, поэтому live prices не расходуют тот же REST budget, что и historical fetch, и при этом lag по thin markets лучше, чем на broad all-ticker last-price feed.

## Правило для ModelLine

- Bybit и Binance считаются full-feed биржами: `klines + funding + open interest`.
- Kraken считается OHLC-only биржей.
- Если новая фича требует funding/OI для Kraken, текущий public path этого не даёт и нужно проектировать отдельный источник данных, а не пытаться «дожать» текущий client.