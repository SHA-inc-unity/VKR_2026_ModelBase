# shared — `modelline_shared`

**Роль:** Общий Python-пакет для Python-частей платформы и локальных инструментов. Содержит контракты обмена сообщениями и messaging-утилиты; не является общим SDK для .NET- и Next.js-сервисов.

## Документация для агентов

- [STRUCTURE.md](STRUCTURE.md) — карта модулей пакета и shared-контрактов
- [../docs/agents/services/shared.md](../docs/agents/services/shared.md) — профиль каталога для agent workflow
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first маршрут работы

## Установка (local dev)

```powershell
pip install -e ./shared
```

## Модули

| Модуль | Описание |
|--------|----------|
| `modelline_shared.schemas` | Pydantic BaseModel: `HealthResponse` и будущие shared-контракты |
| `modelline_shared.messaging.schemas` | `Envelope` (универсальный Kafka-конверт), `HealthReply` |
| `modelline_shared.messaging.topics` | Константы топиков Kafka (`CMD_DATA_*`, `CMD_ANALYTICS_*`, `EVT_*`) + `reply_inbox()` |
| `modelline_shared.messaging.client` | `KafkaClient` (aiokafka): request/reply, pub/sub, регистрация хэндлеров. Хэндлеры исполняются через `asyncio.create_task` — consume-loop никогда не блокируется, что исключает дедлок при вызове `client.request()` внутри хэндлера. **JSON hot-path:** при наличии `orjson` сериализация/парсинг сообщений идут через него (быстрее в ~2–3×); fallback на stdlib `json` использует `separators=(",", ":")` чтобы wire-формат был байт-в-байт совместим. На reply-пути парсится только `correlation_id` + `payload` из dict — без построения полного `Envelope` (Pydantic-валидация replies удалена как лишняя работа на горячем пути; продакшн-валидация остаётся для входящих команд через `Envelope.model_validate`). |

## Kafka-конвенция именования топиков

```
cmd.<service>.<action>        — команда с ожидаемым ответом
reply.<requester>.<instance>  — приватный inbox для reply
events.<service>.<event>      — fire-and-forget события
```
