# shared — `modelline_shared`

**Роль:** Общий Python-пакет для всех ML-микросервисов платформы (`microservice_data`, `microservice_analitic`, `microservice_admin`). Содержит контракты обмена сообщениями и Kafka-клиент.

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
| `modelline_shared.messaging.client` | `KafkaClient` (aiokafka): request/reply, pub/sub, регистрация хэндлеров |

## Kafka-конвенция именования топиков

```
cmd.<service>.<action>        — команда с ожидаемым ответом
reply.<requester>.<instance>  — приватный inbox для reply
events.<service>.<event>      — fire-and-forget события
```
