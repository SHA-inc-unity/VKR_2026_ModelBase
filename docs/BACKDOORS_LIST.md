# BACKDOORS LIST — известные/отложенные проблемы безопасности

Реестр security-проблем, найденных при аудите, которые **сознательно отложены**
(по решению владельца) или ждут координации. Закрытые пункты помечены `✅` с датой.
Не удаляй закрытые — это история.

> Заводи сюда любую новую «дыру», которую решено не чинить прямо сейчас:
> формат — severity, где (`file:line`/сервис), суть, почему отложено, как чинить.

## Открытые (отложены)

- **[high] Нет rate-limit / lockout на аутентификации.** `microservice_account` `/api/account/login` (+ `/register`) и gateway не имеют ASP.NET RateLimiter и счётчика неудачных входов → брутфорс без ограничений (включая подбор admin-пароля).
  - *Почему отложено:* решение владельца — «покачто пусть будет».
  - *Как чинить:* `builder.Services.AddRateLimiter(...)` + `app.UseRateLimiter()` (per-IP fixed-window на `/login`,`/register`) и lockout-счётчик неудачных попыток на записи пользователя.

## Закрыто

- ✅ **2026-06-01 — дефолтный `admin/admin`.** Account-сервис теперь fail-fast вне Development при попытке создать дефолтный admin (нужен `ADMIN_BOOTSTRAP_PASSWORD`); лончер спрашивает пароль при старте с нуля. Существующие admin-аккаунты не трогаются.
- ✅ **2026-06-01 — fallback мастер-ключа шифрования.** Вне Development сервис не стартует без `ACCOUNT_API_KEY_MASTER_KEY` (раньше молча брал литерал из репозитория); лончер спрашивает ключ (по умолчанию — текущий `INTERNAL_API_KEY`, чтобы не сломать уже зашифрованные строки).
- ✅ **2026-06-01 — CORS `AllowAnyOrigin`.** Gateway сужен до `https://sha-trade.tech` + `https://www.sha-trade.tech` (нативные клиенты Origin не шлют — не затронуты).
- ✅ **2026-06-01 — таймингованное сравнение `X-Internal-Api-Key`.** Переведено на `CryptographicOperations.FixedTimeEquals`.
- ✅ **2026-06-01 — DoS-усиление на `/api/v1/market/tickers`.** `page`/`pageSize` клампятся.
- 🔄 **Redpanda `:9092`/`:9644` наружу без auth, MinIO дефолт-креды + anonymous-bucket, TLS-проверка admin→facade отключена** — в работе (см. CHANGE_LOG; инфра-правки и TLS-серт).
