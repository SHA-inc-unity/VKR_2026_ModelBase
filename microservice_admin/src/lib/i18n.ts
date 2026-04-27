/**
 * Flat translation dictionary for the admin panel.
 * Keys use dot-notation groups: nav.*, dashboard.*, dataset.*, train.*, compare.*, anomaly.*
 */

export type Locale = 'en' | 'ru';

const translations = {
  en: {
    // ── Nav ──────────────────────────────────────────────────────────────
    'nav.dashboard': 'Dashboard',
    'nav.dataset':   'Dataset',
    'nav.train':     'Train',
    'nav.compare':   'Compare',
    'nav.anomaly':   'Anomaly',
    'nav.kafka':     'Kafka',

    // ── Common ───────────────────────────────────────────────────────────
    'common.refresh':   'Refresh',
    'common.online':    'Online',
    'common.error':     'Error',
    'common.loading':   'Loading…',
    'common.lastSeen':  'Last seen',
    'common.noData':    '–',
    'common.symbol':    'Symbol',
    'common.timeframe': 'Timeframe',
    'common.dateFrom':  'Date From',
    'common.dateTo':    'Date To',
    'common.history':   'History',
    'common.result':    'Result',
    'common.time':      'Time',
    'common.status':    'Status',
    'common.progress':  'Progress',

    // ── Dashboard ────────────────────────────────────────────────────────
    'dashboard.title':        'Dashboard',
    'dashboard.totalTables':  'Total Tables',
    'dashboard.totalRows':    'Total Rows',
    'dashboard.lastIngestion':'Last Ingestion',
    'dashboard.modelsTrained':'Models Trained',
    'dashboard.services':     'Services',
    'dashboard.coverage':     'Coverage',
    'dashboard.tables':       'Tables',
    'dashboard.infrastructure':'Infrastructure',

    // ── Dataset ──────────────────────────────────────────────────────────
    'dataset.title':          'Dataset',
    'dataset.download':       'Download',
    'dataset.startDownload':  'Start Download',
    'dataset.downloading':    'Downloading…',
    'dataset.tables':         'Tables',
    'dataset.coverage':       'Coverage',
    'dataset.rows':           'Rows',
    'dataset.from':           'From',
    'dataset.to':             'To',
    'dataset.noTables':       'No tables found',
    'dataset.deleteTable':    'Delete table',
    'dataset.confirmDelete':  'Are you sure you want to delete this table?',

    // ── Train ────────────────────────────────────────────────────────────
    'train.title':          'Model Training',
    'train.newTraining':    'New Training',
    'train.history':        'History',
    'train.config':         'Train Configuration',
    'train.start':          'Start Training',
    'train.running':        'Training…',
    'train.noRuns':         'No training runs yet',
    'train.noRunsHint':     'Start a training session to see results here',
    'train.modelId':        'Model ID',
    'train.last20':         'Last 20 runs',
    'train.colTime':        'Time',
    'train.colSymbol':      'Symbol',
    'train.colTF':          'TF',
    'train.colDates':       'Dates',
    'train.colResult':      'Result',
    'train.colMs':          'ms',
    'train.started':        'Training started — polling for status…',

    // ── Compare ──────────────────────────────────────────────────────────
    'compare.title':        'Compare Models',
    'compare.selectModel':  'Select model',
    'compare.modelA':       'Model A',
    'compare.modelB':       'Model B',
    'compare.compare':      'Compare',
    'compare.comparing':    'Comparing…',
    'compare.metrics':      'Metrics',
    'compare.noModels':     'No models available',
    'compare.selectBoth':   'Select both models to compare',

    // ── Anomaly ──────────────────────────────────────────────────────────
    'anomaly.title':         'Anomaly Detection',
    'anomaly.detect':        'Detect',
    'anomaly.detecting':     'Detecting…',
    'anomaly.browse':        'Browse',
    'anomaly.clean':         'Clean',
    'anomaly.columnStats':   'Column Statistics',
    'anomaly.histogram':     'Histogram',
    'anomaly.results':       'Anomaly Results',
    'anomaly.noResults':     'No anomalies found',
    'anomaly.severity':      'Severity',
    'anomaly.type':          'Type',
    'anomaly.column':        'Column',
    'anomaly.value':         'Value',
    'anomaly.details':       'Details',
    'anomaly.totalRows':     'Total Rows',
    'anomaly.page':          'Page',
    'anomaly.cleanApply':    'Apply Clean',
    'anomaly.cleaning':      'Cleaning…',
  },

  ru: {
    // ── Nav ──────────────────────────────────────────────────────────────
    'nav.dashboard': 'Главная',
    'nav.dataset':   'Датасет',
    'nav.train':     'Обучение',
    'nav.compare':   'Сравнение',
    'nav.anomaly':   'Аномалии',
    'nav.kafka':     'Kafka',

    // ── Common ───────────────────────────────────────────────────────────
    'common.refresh':   'Обновить',
    'common.online':    'Онлайн',
    'common.error':     'Ошибка',
    'common.loading':   'Загрузка…',
    'common.lastSeen':  'Последний раз',
    'common.noData':    '–',
    'common.symbol':    'Символ',
    'common.timeframe': 'Таймфрейм',
    'common.dateFrom':  'Дата начала',
    'common.dateTo':    'Дата конца',
    'common.history':   'История',
    'common.result':    'Результат',
    'common.time':      'Время',
    'common.status':    'Статус',
    'common.progress':  'Прогресс',

    // ── Dashboard ────────────────────────────────────────────────────────
    'dashboard.title':        'Главная',
    'dashboard.totalTables':  'Таблицы',
    'dashboard.totalRows':    'Строки всего',
    'dashboard.lastIngestion':'Последнее обновление',
    'dashboard.modelsTrained':'Обученных моделей',
    'dashboard.services':     'Сервисы',
    'dashboard.coverage':     'Покрытие',
    'dashboard.tables':       'Таблицы',
    'dashboard.infrastructure':'Инфраструктура',

    // ── Dataset ──────────────────────────────────────────────────────────
    'dataset.title':          'Датасет',
    'dataset.download':       'Скачать',
    'dataset.startDownload':  'Начать загрузку',
    'dataset.downloading':    'Загружаю…',
    'dataset.tables':         'Таблицы',
    'dataset.coverage':       'Покрытие',
    'dataset.rows':           'Строк',
    'dataset.from':           'С',
    'dataset.to':             'По',
    'dataset.noTables':       'Таблицы не найдены',
    'dataset.deleteTable':    'Удалить таблицу',
    'dataset.confirmDelete':  'Удалить таблицу? Это действие нельзя отменить.',

    // ── Train ────────────────────────────────────────────────────────────
    'train.title':          'Обучение модели',
    'train.newTraining':    'Новое обучение',
    'train.history':        'История',
    'train.config':         'Параметры обучения',
    'train.start':          'Запустить обучение',
    'train.running':        'Обучаю…',
    'train.noRuns':         'Обучений ещё не было',
    'train.noRunsHint':     'Запустите обучение, чтобы увидеть результаты',
    'train.modelId':        'ID модели',
    'train.last20':         'Последние 20 запусков',
    'train.colTime':        'Время',
    'train.colSymbol':      'Символ',
    'train.colTF':          'ТФ',
    'train.colDates':       'Даты',
    'train.colResult':      'Результат',
    'train.colMs':          'мс',
    'train.started':        'Обучение запущено — ожидаю статус…',

    // ── Compare ──────────────────────────────────────────────────────────
    'compare.title':        'Сравнение моделей',
    'compare.selectModel':  'Выбрать модель',
    'compare.modelA':       'Модель A',
    'compare.modelB':       'Модель B',
    'compare.compare':      'Сравнить',
    'compare.comparing':    'Сравниваю…',
    'compare.metrics':      'Метрики',
    'compare.noModels':     'Нет доступных моделей',
    'compare.selectBoth':   'Выберите обе модели для сравнения',

    // ── Anomaly ──────────────────────────────────────────────────────────
    'anomaly.title':         'Обнаружение аномалий',
    'anomaly.detect':        'Найти',
    'anomaly.detecting':     'Ищу…',
    'anomaly.browse':        'Просмотр',
    'anomaly.clean':         'Очистка',
    'anomaly.columnStats':   'Статистика столбцов',
    'anomaly.histogram':     'Гистограмма',
    'anomaly.results':       'Результаты',
    'anomaly.noResults':     'Аномалий не найдено',
    'anomaly.severity':      'Критичность',
    'anomaly.type':          'Тип',
    'anomaly.column':        'Столбец',
    'anomaly.value':         'Значение',
    'anomaly.details':       'Детали',
    'anomaly.totalRows':     'Всего строк',
    'anomaly.page':          'Страница',
    'anomaly.cleanApply':    'Применить очистку',
    'anomaly.cleaning':      'Очищаю…',
  },
} as const;

export type TranslationKey = keyof typeof translations.en;

export function getTranslator(locale: Locale) {
  const dict = translations[locale] as Record<string, string>;
  return (key: TranslationKey): string => dict[key] ?? key;
}
