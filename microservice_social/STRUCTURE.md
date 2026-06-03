# microservice_social — Структура

> **Read before / update after.** Обновляй этот файл при изменении слоёв Clean Architecture,
> `/api/social/*` или `/internal/*` маршрутов, сущностей/конфигураций EF, миграций, Kafka-контракта
> или состава файлов. Код не считается завершённым, пока STRUCTURE не совпадает с кодом.

---

## Связанная документация

- [README.md](README.md) — что это, endpoint-ы, модель данных, runbook, ограничения
- [../docs/agents/services/microservice_social.md](../docs/agents/services/microservice_social.md) — агентный профиль сервиса
- [../docs/agents/WORKFLOW.md](../docs/agents/WORKFLOW.md) — общий docs-first workflow

---

## Слои (Clean Architecture)

```text
SocialService.Domain          ← сущности и доменные правила (без зависимостей)
   ▲
SocialService.Application      ← интерфейсы, app-сервисы, DTO, валидаторы, исключения
   ▲
SocialService.Infrastructure   ← EF Core DbContext, конфигурации, репозитории, миграции
   ▲
SocialService.API              ← хост ASP.NET Core: контроллеры, DI, JWT, Kafka, миграции на старте
```

Зависимости направлены внутрь: `API` → `Infrastructure` + `Application`; `Infrastructure` → `Application`;
`Application` → `Domain`. `Domain` ни от чего не зависит.

---

## Корень сервиса

| Файл | Назначение |
| ---- | ---------- |
| `SocialService.sln` | Solution-файл .NET |
| `Dockerfile` | Multi-stage build: SDK 8.0 publish → aspnet 8.0 runtime, non-root `appuser`, `EXPOSE 5000` |
| `docker-compose.yml` | Локальный стек: `social-api` (`social_service_api`, `7530→5000`) + `postgres` (`social_postgres`); сети `social_net` + внешняя `modelline_net` |
| `.env.example` | Шаблон переменных окружения (порт, БД, JWT, Kafka, internal key) |
| `global.json` | Привязка .NET SDK |
| `README.md` | Основная документация сервиса |
| `STRUCTURE.md` | Этот файл |

---

## src/SocialService.Domain/

Чистые сущности; вся валидация значений (target type, vote) живёт здесь как статические фабрики.

| Файл | Назначение |
| ---- | ---------- |
| `Entities/Comment.cs` | Комментарий: `Create`/`UpdateBody`/`SoftDelete`, `NormalizeTargetType` (`asset`\|`news`), `TargetTypes`. Threaded через `ParentId`. |
| `Entities/CommentLike.cs` | Лайк комментария (`{CommentId, UserId}`) — фабрика `Create`. |
| `Entities/Favorite.cs` | Избранный символ пользователя; символ нормализуется в upper-case. |
| `Entities/AssetSentiment.cs` | Голос bullish/bearish: `Create`/`Change`, `NormalizeVote` (только `bullish`/`bearish` персистятся), `Votes` (включая sentinel `none`). Один голос на (user, target), без суточного сброса. |

---

## src/SocialService.Application/

| Папка / файл | Назначение |
| ------------ | ---------- |
| `Services/CommentsAppService.cs` | List (с пагинацией, лайк/реплай-счётчиками, `likedByMe`, резолвом авторов), Create (политика 1-уровневых реплаев), Update/Delete (author-or-admin), Like/Unlike, `GetAuthorAsync`. Публикует `comment.created` / `comment.liked`. |
| `Services/FavoritesAppService.cs` | List/Add/Remove (идемпотентно), `UsersBySymbolAsync`, `AllFavoritedSymbolsAsync`. Публикует `favorite.added` / `favorite.removed`. |
| `Services/AssetSentimentAppService.cs` | Get (aggregate + `myVote`), Vote (`none`→delete, `bullish`/`bearish`→upsert, затем свежий aggregate). |
| `Interfaces/Services/ISocialServices.cs` | `IFavoritesAppService`, `ICommentsAppService`, `IAssetSentimentAppService`. |
| `Interfaces/Services/IEventBus.cs` | Абстракция Kafka-публикации (`PublishAsync(type, payload, ct)`). |
| `Interfaces/Services/IUserDirectoryService.cs` | Резолв `UserSummary` по списку user id (реализация — HTTP к Account). |
| `Interfaces/Repositories/*` | `ICommentRepository`, `ICommentLikeRepository`, `IFavoriteRepository`, `IAssetSentimentRepository` (+ `SentimentCounts`, `CommentSortMode`). |
| `DTOs/Requests/CreateCommentRequest.cs` | `CreateCommentRequest` + `UpdateCommentRequest`. |
| `DTOs/Requests/SentimentVoteRequest.cs` | `targetType`/`targetId`/`vote` (`bullish`\|`bearish`\|`none`). |
| `DTOs/Responses/CommentResponse.cs` | `CommentResponse` / `CommentListResponse` / `CommentAuthorDto` / `FavoritesResponse`. |
| `DTOs/Responses/SentimentResponse.cs` | `{ bullish, bearish, total, myVote }`. |
| `Validators/CreateCommentRequestValidator.cs` | FluentValidation для создания комментария. |
| `Common/Exceptions/SocialException.cs` | Доменные исключения (`CommentNotFoundException`, `InvalidCommentTargetException`, `ForbiddenSocialActionException`, …) → HTTP-коды в middleware. |
| `Common/Settings/JwtSettings.cs` | `Jwt:SecretKey` / `Issuer` (`account-service`) / `Audience` (`exchange-app`). |

---

## src/SocialService.Infrastructure/

| Папка / файл | Назначение |
| ------------ | ---------- |
| `Data/SocialDbContext.cs` | `DbSet`-ы (Favorites/Comments/CommentLikes/AssetSentiments); применяет все `IEntityTypeConfiguration` из сборки. |
| `Data/Configurations/CommentConfiguration.cs` | Маппинг `comments` + `comment_likes` (`{comment_id,user_id}` PK = дедуп) + `asset_sentiment` (`{user_id,target_type,target_id}` PK = один голос; индекс `{target_type,target_id}` под GROUP BY). |
| `Data/Configurations/FavoriteConfiguration.cs` | Маппинг `favorites` (`{user_id, symbol}` PK, индекс по `symbol`). |
| `Repositories/CommentRepository.cs` | Запросы по комментариям: list/slice, like/reply counts, `WhichLikedByAsync`, get/add/update. |
| `Repositories/FavoriteRepository.cs` | Символы пользователя, exists/add/remove, `GetUsersBySymbolAsync` (base↔quote матчинг), `GetAllDistinctSymbolsAsync`. |
| `Repositories/AssetSentimentRepository.cs` | `CountAsync` (`GROUP BY vote`), `GetVoteAsync`, `UpsertAsync`, `DeleteAsync`. |
| `Migrations/20260524000001_InitialCreate.cs` (+ `.Designer.cs`) | Первичная схема: favorites/comments/comment_likes. |
| `Migrations/20260603000001_AddAssetSentiment.cs` (+ `.Designer.cs`) | Таблица `asset_sentiment`. |
| `Migrations/SocialDbContextModelSnapshot.cs` | Снимок модели EF (правится синхронно с новыми миграциями). |

---

## src/SocialService.API/

| Папка / файл | Назначение |
| ------------ | ---------- |
| `Program.cs` | Bootstrap: Serilog, DI (`AddSocialServices`), controllers, Swagger, health checks, middleware pipeline, `UseAuthentication`/`UseAuthorization`, `MapHealthChecks("/health")`, и `await app.MigrateAndSeedAsync()` перед запуском. |
| `Extensions/ServiceCollectionExtensions.cs` | Регистрация всех зависимостей: options (Jwt/Kafka/AccountService), `KafkaEventBus`, `SocialDbContext` (Npgsql + snake_case), репозитории, app-сервисы, `HttpUserDirectoryService` (typed HttpClient, 5s timeout), FluentValidation, JWT Bearer (HS256, issuer/audience валидация, 30s clock skew), Swagger. |
| `Extensions/MigrationExtensions.cs` | `MigrateAndSeedAsync`: логирует pending миграции, `MigrateAsync`, затем проверяет `RequiredTables` whitelist (`favorites`, `comments`, `comment_likes`, `asset_sentiment`) — всё отсутствует → пересоздать схему, частично → throw. **Новую таблицу обязательно добавлять сюда.** |
| `Controllers/CommentsController.cs` | `/api/social/comments` — list (anon), create/patch/delete/like/unlike (JWT), извлечение `userId`/`isAdmin` из claims. |
| `Controllers/FavoritesController.cs` | `/api/social/favorites` — list/put/delete (весь контроллер `[Authorize]`). |
| `Controllers/SentimentController.cs` | `/api/social/sentiment` — GET (anon, `myVote` если есть JWT), POST vote (JWT). |
| `Controllers/InternalController.cs` | `/internal/*` под `X-Internal-Api-Key` (author by comment id, users by symbol, all favorited symbols) — для `microservice_notification`. |
| `Kafka/KafkaSettings.cs` | `Kafka:BootstrapServers` + `SocialEventsTopic` (`events.social.v1`). |
| `Kafka/KafkaEventBus.cs` | Producer; конверт `{ type, occurredAt, payload }` (camelCase JSON); **best-effort** — логирует и глотает ошибки публикации. |
| `Services/HttpUserDirectoryService.cs` | HTTP к Account `/internal/users/{id}` с `X-Internal-Api-Key`; soft-fail (`unknown` автор при недоступности). Содержит `AccountServiceSettings`. |
| `Middleware/GlobalExceptionMiddleware.cs` | Маппинг доменных исключений в HTTP-ответы. |

---

## Поток событий

`CommentsAppService` / `FavoritesAppService` → `IEventBus` (`KafkaEventBus`) →
Kafka topic **`events.social.v1`** → consumer `microservice_notification`. Типы событий:
`comment.created`, `comment.liked`, `favorite.added`, `favorite.removed`. Sentiment-голоса событий
не публикуют. Публикация best-effort: падение брокера не ломает HTTP-запрос.
