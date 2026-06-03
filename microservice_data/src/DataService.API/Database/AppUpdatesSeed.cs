namespace DataService.API.Database;

/// <summary>
/// Compile-time seed for the append-only app-updates store. This is a faithful
/// C# mirror of the Flutter client's bundled changelog
/// (<c>VKR_2026_crypt/lib/core/changelog/app_changelog.dart</c>,
/// <c>kAppReleases</c>) so the data-service can serve the same "What's New"
/// history over Kafka (<c>cmd.data.updates.list</c>) without the client
/// hardcoding it.
///
/// Newest release first. Release <see cref="SeedRelease.SortIndex"/> is highest
/// for the newest release; change <see cref="SeedChange.SortIndex"/> is the
/// change's authored position within its build. Text is Russian — kept verbatim.
///
/// The store is append-only: once these rows are seeded (or appended later via
/// <see cref="AppUpdatesRepository.AppendReleaseAsync"/> /
/// <see cref="AppUpdatesRepository.AppendChangeAsync"/>) they cannot be updated
/// or deleted (DB-level trigger guard). Editing this file therefore only
/// affects a fresh, empty store; it will not retro-edit already-seeded rows.
/// </summary>
public static class AppUpdatesSeed
{
    // change_type values
    public const string TypeFeature     = "feature";
    public const string TypeFix         = "fix";
    public const string TypeImprovement = "improvement";

    // scope values
    public const string ScopeFrontend = "frontend";
    public const string ScopeBackend  = "backend";
    public const string ScopeInternal = "internal";

    public sealed record SeedChange(string Type, string Scope, string Text, int SortIndex);

    public sealed record SeedBuild(int BuildNumber, string BuildDate, IReadOnlyList<SeedChange> Changes);

    public sealed record SeedRelease(
        string Version,
        string Title,
        string ReleaseDate,
        IReadOnlyList<string> Highlights,
        int SortIndex,
        IReadOnlyList<SeedBuild> Builds);

    /// <summary>Builds a change list, assigning sort_index = authored position.</summary>
    private static IReadOnlyList<SeedChange> Changes(params (string Type, string Scope, string Text)[] items)
    {
        var list = new List<SeedChange>(items.Length);
        for (var i = 0; i < items.Length; i++)
            list.Add(new SeedChange(items[i].Type, items[i].Scope, items[i].Text, i));
        return list;
    }

    /// <summary>The bundled changelog, newest release first (mirrors kAppReleases).</summary>
    public static readonly IReadOnlyList<SeedRelease> Releases = new List<SeedRelease>
    {
        new(
            Version: "0.2.3",
            Title: "Этап 2: лидеры рынка, push и ценовые алерты",
            ReleaseDate: "2026-06-03",
            Highlights: new[]
            {
                "🎯 Ценовые алерты теперь реально срабатывают: задайте цель на экране «Уведомления» — придёт уведомление, когда цена её достигнет",
                "🔔 Push-уведомления приходят, даже когда приложение закрыто (браузер / Android-PWA)",
                "📈 Топ растущих и падающих монет прямо на главной",
                "📊 Изменение цены за 1ч / 7д / 30д на странице монеты",
                "🗂 Фильтр по категориям/секторам на рынке (Layer 1, DeFi, ИИ, мемы, Solana и др.)",
                "📋 Вкладка «Обновления» — теперь видно, что нового в приложении",
            },
            SortIndex: 3,
            Builds: new[]
            {
                new SeedBuild(56, "2026-06-03", Changes(
                    (TypeFix, ScopeBackend,
                        "Пакет исправлений по аудиту: устранены гонки (social — лайки/избранное/сентимент, notification — push-подписки), утечка Web Push-клиента; добавлены rate-limit и отзыв access-токена (account)"),
                    (TypeFix, ScopeBackend,
                        "Аудит: news больше не теряет длинные заголовки, analitic — починен плановый ретрейн и /retrain больше не блокирует сервис, data — увеличен пул соединений"),
                    (TypeFix, ScopeFrontend,
                        "Аудит: устойчивость экранов (нет сбоя при выходе во время загрузки), прогноз и портфель идут через слой репозитория (кеш + корректный профиль пользователя)"),
                    (TypeImprovement, ScopeInternal,
                        "Аудит: admin больше не отключает проверку TLS глобально; append-only хранилище обновлений теперь поддерживает дозапись новых записей"))),
                new SeedBuild(55, "2026-06-03", Changes(
                    (TypeFeature, ScopeBackend,
                        "«Обновления» переехали в append-only таблицу data-сервиса (две таблицы + запрет UPDATE/DELETE/TRUNCATE на уровне Postgres): историю релизов нельзя стереть или переписать — только добавлять"),
                    (TypeImprovement, ScopeFrontend,
                        "Экран «Обновления» теперь загружает историю с бэкенда (с оффлайн-фолбэком на встроенный список)"))),
                new SeedBuild(54, "2026-06-03", Changes(
                    (TypeFix, ScopeInternal,
                        "Восстановлены релизы 0.2.1 и 0.2.2 в истории обновлений (были ошибочно свёрнуты в 0.2.3)"))),
                new SeedBuild(53, "2026-06-03", Changes(
                    (TypeImprovement, ScopeInternal,
                        "Версия 0.2.2 → 0.2.3: завершён этап 2 (лидеры рынка, %-окна 1ч/7д/30д, категории, push, ценовые алерты)"))),
            }),

        new(
            Version: "0.2.2",
            Title: "История обновлений",
            ReleaseDate: "2026-06-03",
            Highlights: new[]
            {
                "📋 Появилась вкладка «Обновления» — теперь видно, что нового в приложении",
            },
            SortIndex: 2,
            Builds: new[]
            {
                new SeedBuild(52, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Ценовые уведомления теперь реально срабатывают: алерт, заданный на экране «Уведомления», отслеживает цену и присылает уведомление (в ленту и push) при достижении цели"),
                    (TypeFeature, ScopeBackend,
                        "Движок оценки ценовых алертов в notification-сервисе: durable-хранилище (таблица price_alerts), фоновый evaluator опрашивает включённые алерты по нашему снапшоту рынка и срабатывает один раз с авто-перевзводом при возврате цены; gateway форвардит /api/alerts в notification (контракт не изменился)"))),
                new SeedBuild(51, "2026-06-03", Changes(
                    (TypeFix, ScopeFrontend,
                        "Топ движений / Лидеры роста больше не залипают пустыми после рестарта бэкенда — пустые секции не кэшируются, следующий запрос подтягивает данные"))),
                new SeedBuild(50, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Push-уведомления: включаются в настройках уведомлений, приходят даже при закрытом приложении (браузер / Android-PWA)"),
                    (TypeFeature, ScopeBackend,
                        "Self-hosted Web Push (VAPID, без Firebase): подписки в нашем Postgres, отправка из notification-сервиса при создании уведомления (зеркалит SSE)"))),
                new SeedBuild(49, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Фильтр по категориям/секторам на экране рынка (Layer 1, DeFi, ИИ, мемы, Solana и др.)"),
                    (TypeFeature, ScopeBackend,
                        "Курируемая карта категорий (наши данные, без внешних API): серверный фильтр ?category= + эндпоинт /categories"))),
                new SeedBuild(48, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Изменение цены за 1ч / 24ч / 7д / 30д на странице монеты (нет истории за окно → «—»)"),
                    (TypeFeature, ScopeBackend,
                        "Мульти-окно % (1ч/7д/30д) считается из НАШИХ свечей (microservice_data) через cmd.data.dataset.latest_rows — без внешних API; 24ч прежний"))),
                new SeedBuild(47, "2026-06-03", Changes(
                    (TypeFix, ScopeFrontend,
                        "Растущие/Падающие: переключение мгновенное — цена и 24h % теперь живые сразу (обе вкладки остаются активными)"),
                    (TypeImprovement, ScopeFrontend,
                        "Убран повторяющийся заголовок «Взгляд инвестора» на блоках главной"),
                    (TypeImprovement, ScopeFrontend,
                        "В Настройках убран дубль входа в «Обновления» (остался тап по версии приложения)"),
                    (TypeImprovement, ScopeInternal,
                        "Пользователю sha выданы права администратора"))),
                new SeedBuild(46, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Вкладка «Обновления»: публичные релизы для всех, детали сборок для админов"),
                    (TypeImprovement, ScopeInternal,
                        "Версия приложения 0.2.1 → 0.2.2"))),
            }),

        new(
            Version: "0.2.1",
            Title: "Лидеры рынка и стабильность",
            ReleaseDate: "2026-06-03",
            Highlights: new[]
            {
                "📈 Топ растущих и падающих монет прямо на главной",
            },
            SortIndex: 1,
            Builds: new[]
            {
                new SeedBuild(45, "2026-06-03", Changes(
                    (TypeImprovement, ScopeFrontend,
                        "Единый формат чисел во всём приложении"),
                    (TypeFix, ScopeFrontend,
                        "Исправлен редкий сбой при выходе с экрана во время загрузки"),
                    (TypeImprovement, ScopeInternal,
                        "Рефакторинг: декомпозиция крупных экранов, удаление дублей"))),
                new SeedBuild(44, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Секция «Лидеры роста/падения» на главной"),
                    (TypeFeature, ScopeBackend,
                        "Новые эндпоинты gateway /gainers и /losers поверх собственного снапшота рынка"))),
            }),

        new(
            Version: "0.2.0",
            Title: "Реальные рыночные данные и соцфункции",
            ReleaseDate: "2026-06-03",
            Highlights: new[]
            {
                "💰 Настоящая капитализация монет + circulating/total/max supply, FDV и исторический максимум (ATH)",
                "📊 Глобальная статистика: капитализация рынка и доминация BTC",
                "🔀 Сравнение монет на одном графике",
                "👍 Голосование за настроение по монете (рост/падение)",
                "⚡ Мгновенное переключение вкладок без перезагрузок",
                "🔑 Тихое продление сессии — больше не разлогинивает",
            },
            SortIndex: 0,
            Builds: new[]
            {
                new SeedBuild(43, "2026-06-03", Changes(
                    (TypeFeature, ScopeFrontend,
                        "Система версий приложения + версия в углу панели навигации"))),
                new SeedBuild(42, "2026-06-03", Changes(
                    (TypeFix, ScopeBackend,
                        "Исправлен 403 от CoinGecko /global (User-Agent) → реальные капитализация и доминация"),
                    (TypeFeature, ScopeBackend,
                        "Сервис метаданных монет: supply/FDV/ATH из CoinGecko по курируемой карте"),
                    (TypeFeature, ScopeBackend,
                        "Сервис настроения (microservice_social): таблица asset_sentiment + роуты gateway"),
                    (TypeFix, ScopeBackend,
                        "Serve-stale-on-error для глобальной статистики — карточка не схлопывается до 4 элементов"),
                    (TypeImprovement, ScopeFrontend,
                        "Миграция 11 экранов на screen-local Cubit (единая архитектура)"),
                    (TypeImprovement, ScopeFrontend,
                        "Keep-alive навигация + кеширование для мгновенного UX"),
                    (TypeFeature, ScopeFrontend,
                        "Чип сортировки по капитализации, supply/FDV/ATH на странице монеты, бар настроения"),
                    (TypeFix, ScopeFrontend,
                        "Тихое продление токена (single-flight refresh)"))),
            }),
    };
}
