"""Двуязычный интерфейс ModelLine (RU / EN).

Использование::

    from services.i18n import t, get_lang, set_lang, LANGS

    st.title(t("app.title"))
    lang = get_lang()          # "ru" | "en"
    set_lang("en")             # сохраняет в store + session_state

Ключи организованы по страницам:
    common.*        — общие элементы (кнопки, статусы, поля)
    app.*           — главная страница
    download.*      — страница загрузки данных
    model.*         — страница модели
    compare.*       — страница сравнения

Формат словаря: {"ключ": {"ru": "...", "en": "..."}}
"""
from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Translation table
# ---------------------------------------------------------------------------

_T: dict[str, dict[str, str]] = {

    # ── Common ───────────────────────────────────────────────────────────────
    "common.back":              {"ru": "← Назад",               "en": "← Back"},
    "common.save":              {"ru": "Сохранить",             "en": "Save"},
    "common.clear":             {"ru": "Сбросить",              "en": "Clear"},
    "common.refresh":           {"ru": "Обновить",              "en": "Refresh"},
    "common.connected":         {"ru": "Подключено",            "en": "Connected"},
    "common.failed":            {"ru": "Ошибка",                "en": "Failed"},
    "common.status":            {"ru": "Статус",                "en": "Status"},
    "common.host":              {"ru": "Хост",                  "en": "Host"},
    "common.port":              {"ru": "Порт",                  "en": "Port"},
    "common.database":          {"ru": "База данных",           "en": "Database"},
    "common.user":              {"ru": "Пользователь",          "en": "User"},
    "common.password":          {"ru": "Пароль",                "en": "Password"},
    "common.symbol":            {"ru": "Инструмент",            "en": "Symbol"},
    "common.timeframe":         {"ru": "Таймфрейм",             "en": "Timeframe"},
    "common.date_from":         {"ru": "Дата начала",           "en": "Start date"},
    "common.date_to":           {"ru": "Дата конца",            "en": "End date"},
    "common.settings_saved":    {"ru": "Настройки сохранены.",  "en": "Settings saved."},
    "common.settings_cleared":  {"ru": "Настройки сброшены.",   "en": "Settings cleared."},
    "common.db_error":          {"ru": "Ошибка подключения к БД", "en": "Database connection failed"},
    "common.db_status":         {"ru": "Статус базы данных",    "en": "Database status"},
    "common.db_settings":       {"ru": "Настройки подключения к PostgreSQL (опционально)",
                                  "en": "PostgreSQL connection settings (optional)"},
    "common.save_conn":         {"ru": "Сохранить настройки подключения",
                                  "en": "Save connection settings"},
    "common.clear_conn":        {"ru": "Сбросить настройки",   "en": "Clear settings"},
    "common.lang_toggle":       {"ru": "EN",                    "en": "RU"},
    "common.rows":              {"ru": "Строк",                 "en": "Rows"},
    "common.table":             {"ru": "Таблица",               "en": "Table"},
    "common.target":            {"ru": "Цель",                  "en": "Target"},
    "common.params":            {"ru": "Параметры",             "en": "Parameters"},

    # ── App (main page) ───────────────────────────────────────────────────────
    "app.title":                {"ru": "ModelLine",             "en": "ModelLine"},
    "app.caption":              {"ru": "CatBoost-модель прогнозирования крипто-инструментов.",
                                  "en": "CatBoost model for crypto instrument return forecasting."},
    "app.btn_download":         {"ru": "📥 Загрузка данных",    "en": "📥 Download data"},
    "app.btn_model":            {"ru": "🤖 Обучение модели",    "en": "🤖 Train model"},
    "app.btn_compare":          {"ru": "📊 Сравнение моделей",  "en": "📊 Compare models"},
    "app.btn_backtest":         {"ru": "🧪 Бэктест (скоро)",    "en": "🧪 Backtest (coming soon)"},
    "app.system_status":        {"ru": "Состояние системы",     "en": "System status"},
    "app.db_connected":         {"ru": "БД подключена",         "en": "DB connected"},
    "app.db_offline":           {"ru": "БД недоступна",         "en": "DB offline"},
    "app.models_count":         {"ru": "Моделей в реестре",     "en": "Models in registry"},
    "app.tables_count":         {"ru": "Таблиц с данными",      "en": "Data tables"},
    "app.store_backend":        {"ru": "Хранилище настроек",    "en": "Settings store"},

    # ── Download page ────────────────────────────────────────────────────────
    "download.title":           {"ru": "Загрузка данных",       "en": "Download data"},
    "download.caption":         {"ru": "Покрытие данных, загрузка пропусков, просмотр датасета и графики.",
                                  "en": "Data coverage, gap filling, dataset preview and charts."},
    "download.params":          {"ru": "Параметры",             "en": "Parameters"},
    "download.btn_check":       {"ru": "🔍 Проверить покрытие", "en": "🔍 Check coverage"},
    "download.btn_download":    {"ru": "⬇ Скачать пропуски",   "en": "⬇ Download missing"},
    "download.btn_load":        {"ru": "📂 Загрузить датасет",  "en": "📂 Load dataset"},
    "download.date_error":      {"ru": "Дата начала не может быть позже даты конца.",
                                  "en": "Start date must not be later than end date."},
    "download.validating":      {"ru": "Проверка таблицы",      "en": "Validating table"},
    "download.checking":        {"ru": "Проверка покрытия...",  "en": "Checking coverage..."},
    "download.downloading":     {"ru": "Загрузка данных...",    "en": "Downloading data..."},
    "download.loading_ds":      {"ru": "Загрузка датасета...",  "en": "Loading dataset..."},
    "download.done":            {"ru": "Загрузка завершена.",   "en": "Download complete."},
    "download.failed":          {"ru": "Ошибка загрузки",       "en": "Download failed"},
    "download.coverage":        {"ru": "Покрытие",              "en": "Coverage"},
    "download.expected":        {"ru": "Ожидается",             "en": "Expected"},
    "download.existing":        {"ru": "Есть",                  "en": "Existing"},
    "download.missing_count":   {"ru": "Пропусков",             "en": "Missing"},
    "download.existing_ranges": {"ru": "Имеющиеся интервалы",   "en": "Existing intervals"},
    "download.missing_ranges":  {"ru": "Пропущенные интервалы", "en": "Missing intervals"},
    "download.dl_result":       {"ru": "Результат загрузки",    "en": "Download result"},
    "download.inserted":        {"ru": "Вставлено строк",       "en": "Inserted rows"},
    "download.updated":         {"ru": "Обновлено строк",       "en": "Updated rows"},
    "download.ds_summary":      {"ru": "Статистика датасета",   "en": "Dataset summary"},
    "download.features":        {"ru": "Признаков",             "en": "Features"},
    "download.columns":         {"ru": "Столбцов",              "en": "Columns"},
    "download.from":            {"ru": "От",                    "en": "From"},
    "download.to":              {"ru": "До",                    "en": "To"},
    "download.missing_vals":    {"ru": "Пропущенные значения",  "en": "Missing values"},
    "download.charts":          {"ru": "Графики",               "en": "Charts"},
    "download.metrics_display": {"ru": "Метрики для отображения", "en": "Metrics to display"},
    "download.overlay":         {"ru": "Совместить на одном графике", "en": "Overlay mode"},
    "download.raw_data":        {"ru": "Сырые данные",          "en": "Raw data"},
    "download.columns_select":  {"ru": "Столбцы для отображения", "en": "Columns to display"},
    "download.no_db":           {"ru": "Задайте переменные окружения PostgreSQL или используйте настройки подключения выше.",
                                  "en": "Set PostgreSQL environment variables or use the connection settings above."},

    # ── Model page ───────────────────────────────────────────────────────────
    "model.title":              {"ru": "CatBoost — прогноз",    "en": "CatBoost — forecast"},
    "model.caption":            {"ru": "Walk-forward split · Редактируемый Half-Grid · TimeSeriesSplit CV · GPU training",
                                  "en": "Walk-forward split · Editable Half-Grid · TimeSeriesSplit CV · GPU training"},
    "model.params":             {"ru": "Параметры",             "en": "Parameters"},
    "model.target_col":         {"ru": "Целевая переменная",    "en": "Target column"},
    "model.target_help":        {"ru": "Колонка-цель из БД. Все target_*-колонки исключаются из признаков X.",
                                  "en": "Target column from DB. All target_* columns are excluded from features X."},
    "model.table_exists":       {"ru": "✅ Данные есть",         "en": "✅ Data available"},
    "model.table_missing":      {"ru": "⚠ Данных нет",          "en": "⚠ No data"},
    "model.bars_year":          {"ru": "Баров/год",             "en": "Bars/year"},
    "model.cv_expander":        {"ru": "Cross-validation (TimeSeriesSplit)", "en": "Cross-validation (TimeSeriesSplit)"},
    "model.cv_mode":            {"ru": "Режим CV",              "en": "CV mode"},
    "model.cv_expanding":       {"ru": "expanding",             "en": "expanding"},
    "model.cv_rolling":         {"ru": "rolling",               "en": "rolling"},
    "model.max_train_size":     {"ru": "max_train_size (баров)", "en": "max_train_size (bars)"},
    "model.max_train_help":     {"ru": "0 = без ограничения. Используется только при rolling.",
                                  "en": "0 = unlimited. Used only with rolling."},
    "model.rolling_info":       {"ru": "Rolling window: {n} баров (≈ {d:.1f} дней)",
                                  "en": "Rolling window: {n} bars (≈ {d:.1f} days)"},
    "model.expanding_info":     {"ru": "Expanding window: train растёт, val фиксирован.",
                                  "en": "Expanding window: train grows, val is fixed."},
    "model.cache_expander":     {"ru": "Кеш датасетов (parquet)", "en": "Dataset cache (parquet)"},
    "model.use_cache":          {"ru": "Использовать disk-кеш", "en": "Use disk cache"},
    "model.use_cache_help":     {"ru": "Сохраняет датасеты в MODELS_DIR/cache/*.parquet.",
                                  "en": "Saves datasets to MODELS_DIR/cache/*.parquet."},
    "model.clear_cache":        {"ru": "🗑 Очистить кеш",       "en": "🗑 Clear cache"},
    "model.cache_cleared":      {"ru": "Удалено {n} файлов кеша.", "en": "Removed {n} cache files."},
    "model.cache_stats":        {"ru": "Записей: **{n}**  ·  Объём: **{mb:.1f} MB**",
                                  "en": "Entries: **{n}**  ·  Size: **{mb:.1f} MB**"},
    "model.date_from_help":     {"ru": "Оставьте пустым — загрузить с начала таблицы",
                                  "en": "Leave empty to load from table start"},
    "model.date_to_help":       {"ru": "Оставьте пустым — загрузить до конца таблицы",
                                  "en": "Leave empty to load until table end"},
    "model.range_all":          {"ru": "Диапазон: **все данные из таблицы**",
                                  "en": "Range: **all data from table**"},
    "model.range_label":        {"ru": "Диапазон: **{r}**",     "en": "Range: **{r}**"},
    "model.reconnect":          {"ru": "🔌 Переподключиться",    "en": "🔌 Reconnect"},
    "model.conn_expander":      {"ru": "Подключение к PostgreSQL", "en": "PostgreSQL connection"},
    "model.save_conn":          {"ru": "Сохранить настройки",   "en": "Save settings"},
    "model.clear_conn":         {"ru": "Очистить настройки",    "en": "Clear settings"},
    "model.conn_saved":         {"ru": "Настройки сохранены.",  "en": "Settings saved."},
    "model.no_data_info":       {"ru": "Данные не загружены. Будут загружены автоматически при старте обучения.",
                                  "en": "No data loaded. Will be downloaded automatically when training starts."},
    "model.btn_load":           {"ru": "⬇ Загрузить датасет",   "en": "⬇ Load dataset"},
    "model.loading":            {"ru": "Загрузка данных...",     "en": "Loading data..."},
    "model.tab_grid":           {"ru": "Half Grid Search",       "en": "Half Grid Search"},
    "model.tab_optuna":         {"ru": "Optuna Search",          "en": "Optuna Search"},
    "model.tab_train":          {"ru": "Финальное обучение",     "en": "Final training"},
    "model.tab_registry":       {"ru": "Реестр моделей",         "en": "Model registry"},
    "model.mlflow_expander":    {"ru": "MLflow (опционально)",   "en": "MLflow (optional)"},
    "model.use_mlflow":         {"ru": "Логировать в MLflow",    "en": "Log to MLflow"},
    "model.mlflow_uri":         {"ru": "MLflow Tracking URI",    "en": "MLflow Tracking URI"},
    "model.mlflow_exp":         {"ru": "Experiment name",        "en": "Experiment name"},
    "model.invalid_uri":        {"ru": "Неверный MLflow URI.",   "en": "Invalid MLflow URI."},
    "model.go_compare":         {"ru": "Перейти к сравнению →",  "en": "Go to compare →"},
    "model.registry_empty":     {"ru": "Реестр пуст. Обучите модель чтобы зарегистрировать версию.",
                                  "en": "Registry is empty. Train a model to register a version."},
    "model.delete_version":     {"ru": "Удалить",               "en": "Delete"},
    "model.version_deleted":    {"ru": "Версия {v} удалена.",    "en": "Version {v} deleted."},
    "model.apply_params":       {"ru": "Применить параметры",    "en": "Apply params"},
    "model.params_applied":     {"ru": "Параметры применены.",   "en": "Parameters applied."},
    "model.reset_grid":         {"ru": "↺ Сбросить к умолчаниям", "en": "↺ Reset to defaults"},
    "model.use_gpu":            {"ru": "Использовать GPU",       "en": "Use GPU"},
    "model.n_trials":           {"ru": "Количество испытаний",   "en": "Number of trials"},
    "model.run_grid":           {"ru": "▶ Запустить Grid Search", "en": "▶ Run Grid Search"},
    "model.run_optuna":         {"ru": "▶ Запустить Optuna",     "en": "▶ Run Optuna"},
    "model.run_train":          {"ru": "▶ Запустить обучение",   "en": "▶ Start training"},
    "model.stop":               {"ru": "⏹ Остановить",           "en": "⏹ Stop"},
    "model.running":            {"ru": "Выполняется...",         "en": "Running..."},
    "model.grid_running":       {"ru": "⏳ Grid Search выполняется...", "en": "⏳ Grid Search running..."},
    "model.optuna_running":     {"ru": "⏳ Optuna Search выполняется...", "en": "⏳ Optuna Search running..."},
    "model.train_running":      {"ru": "⏳ Обучение выполняется...", "en": "⏳ Training running..."},
    "model.load_error":         {"ru": "Ошибка загрузки данных", "en": "Data loading error"},
    "model.load_error_grid":    {"ru": "Ошибка загрузки данных (Grid Search)",
                                  "en": "Data loading error (Grid Search)"},
    "model.load_error_optuna":  {"ru": "Ошибка загрузки данных (Optuna Search)",
                                  "en": "Data loading error (Optuna Search)"},
    "model.load_error_train":   {"ru": "Ошибка загрузки данных (финальное обучение)",
                                  "en": "Data loading error (final training)"},

    # ── Compare page ─────────────────────────────────────────────────────────
    "compare.title":            {"ru": "Сравнение моделей",     "en": "Model comparison"},
    "compare.caption":          {"ru": "Выберите сессии для side-by-side сравнения метрик и графиков.",
                                  "en": "Select sessions for side-by-side metrics and chart comparison."},
    "compare.select":           {"ru": "Сессии для сравнения",  "en": "Sessions to compare"},
    "compare.select_help":      {"ru": "Минимум одна; обычно 2–4 для наглядного overlay.",
                                  "en": "At least one; typically 2–4 for a clear overlay."},
    "compare.no_sessions":      {"ru": "Нет сохранённых сессий. Обучите модель на странице обучения.",
                                  "en": "No saved sessions. Train a model on the model page first."},
    "compare.go_model":         {"ru": "Перейти к обучению →",  "en": "Go to training →"},
    "compare.select_prompt":    {"ru": "Выберите хотя бы одну сессию.",
                                  "en": "Select at least one session."},
    "compare.metrics":          {"ru": "Метрики",               "en": "Metrics"},
    "compare.params":           {"ru": "Параметры",             "en": "Parameters"},
    "compare.pnl":              {"ru": "Cumulative P&L",        "en": "Cumulative P&L"},
    "compare.actual_pred":      {"ru": "Actual vs Predicted",   "en": "Actual vs Predicted"},
    "compare.apply_params":     {"ru": "⚙ Применить параметры к форме обучения",
                                  "en": "⚙ Apply params to training form"},
    "compare.params_applied":   {"ru": "Параметры сессии {p} применены. Перейдите на страницу обучения.",
                                  "en": "Params from session {p} applied. Go to the training page."},
}

# ---------------------------------------------------------------------------
# Language storage key
# ---------------------------------------------------------------------------

_LANG_KEY = "ui:lang"
LANGS = ("ru", "en")
_DEFAULT_LANG = "ru"


def get_lang() -> str:
    """Возвращает текущий язык из session_state (быстро) или store (при первом запуске)."""
    if "ui_lang" in st.session_state:
        return st.session_state["ui_lang"]
    try:
        from services.store import store as _store
        saved = _store.get(_LANG_KEY)
        lang = saved if saved in LANGS else _DEFAULT_LANG
    except Exception:
        lang = _DEFAULT_LANG
    st.session_state["ui_lang"] = lang
    return lang


def set_lang(lang: str) -> None:
    """Устанавливает язык и сохраняет в store."""
    if lang not in LANGS:
        lang = _DEFAULT_LANG
    st.session_state["ui_lang"] = lang
    try:
        from services.store import store as _store
        _store.set(_LANG_KEY, lang)
    except Exception:
        pass


def t(key: str, **kwargs: object) -> str:
    """Возвращает перевод ключа на текущий язык.

    Поддерживает format-параметры::

        t("model.rolling_info", n=5000, d=30.0)
    """
    lang = get_lang()
    entry = _T.get(key)
    if entry is None:
        return key  # fallback: показываем сам ключ
    text = entry.get(lang) or entry.get(_DEFAULT_LANG) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


__all__ = ["t", "get_lang", "set_lang", "LANGS"]
