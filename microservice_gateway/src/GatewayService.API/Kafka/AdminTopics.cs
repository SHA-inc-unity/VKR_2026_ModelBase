namespace GatewayService.API.Kafka;

/// <summary>
/// Kafka topic constants used by the admin backend facade.
/// Mirrors microservice_admin/src/lib/topics.ts (Topics object).
/// </summary>
public static class AdminTopics
{
    // ── Health ────────────────────────────────────────────────────────────────
    public const string DataHealth       = "cmd.data.health";
    public const string AnalyticsHealth  = "cmd.analytics.health";

    // ── Dataset ───────────────────────────────────────────────────────────────
    public const string DatasetListTables      = "cmd.data.dataset.list_tables";
    public const string DatasetCoverage        = "cmd.data.dataset.coverage";
    public const string DatasetRows            = "cmd.data.dataset.rows";
    public const string DatasetExport          = "cmd.data.dataset.export";
    public const string DatasetIngest          = "cmd.data.dataset.ingest";
    public const string DatasetNormalizeTf     = "cmd.data.dataset.normalize_timeframe";
    public const string DatasetMakeTable       = "cmd.data.dataset.make_table_name";
    public const string DatasetInstrument      = "cmd.data.dataset.instrument_details";
    public const string DatasetSchema          = "cmd.data.dataset.table_schema";
    public const string DatasetFindMissing     = "cmd.data.dataset.find_missing";
    public const string DatasetTimestamps      = "cmd.data.dataset.timestamps";
    public const string DatasetConstants       = "cmd.data.dataset.constants";
    public const string DatasetDeleteRows      = "cmd.data.dataset.delete_rows";
    public const string DatasetImportCsv       = "cmd.data.dataset.import_csv";
    public const string DatasetUpsertOhlcv     = "cmd.data.dataset.upsert_ohlcv";

    // ── Anomaly / inspection ──────────────────────────────────────────────────
    public const string DatasetColumnStats      = "cmd.data.dataset.column_stats";
    public const string DatasetColumnHistogram  = "cmd.data.dataset.column_histogram";
    public const string DatasetBrowse           = "cmd.data.dataset.browse";
    public const string DatasetComputeFeatures  = "cmd.data.dataset.compute_features";
    public const string DatasetDetectAnomalies  = "cmd.data.dataset.detect_anomalies";
    public const string DatasetCleanPreview     = "cmd.data.dataset.clean.preview";
    public const string DatasetCleanApply       = "cmd.data.dataset.clean.apply";
    public const string DatasetAuditLog         = "cmd.data.dataset.audit_log";

    // ── Background jobs ───────────────────────────────────────────────────────
    public const string JobsStart  = "cmd.data.dataset.jobs.start";
    public const string JobsCancel = "cmd.data.dataset.jobs.cancel";
    public const string JobsGet    = "cmd.data.dataset.jobs.get";
    public const string JobsList   = "cmd.data.dataset.jobs.list";

    // ── Dedicated market watcher ─────────────────────────────────────────────
    public const string MarketWatcherStatus      = "cmd.data.market_watcher.status";
    public const string MarketWatcherSetEnabled  = "cmd.data.market_watcher.set_enabled";
    public const string MarketWatcherRows        = "cmd.data.market_watcher.rows";
    public const string MarketWatcherLogs        = "cmd.data.market_watcher.logs";

    // ── DB ────────────────────────────────────────────────────────────────────
    public const string DbPing = "cmd.data.db.ping";

    // ── Analitic (dataset session + ML) ──────────────────────────────────────
    public const string AnaliticDatasetLoad             = "cmd.analitic.dataset.load";
    public const string AnaliticDatasetUnload           = "cmd.analitic.dataset.unload";
    public const string AnaliticDatasetStatus           = "cmd.analitic.dataset.status";
    public const string AnaliticAnomalyDbscan           = "cmd.analitic.anomaly.dbscan";
    public const string AnaliticAnomalyIsolationForest  = "cmd.analitic.anomaly.isolation_forest";
    public const string AnaliticDatasetDistribution     = "cmd.analitic.dataset.distribution";
    public const string AnaliticDatasetQualityCheck     = "cmd.analitic.dataset.quality_check";
    public const string AnaliticDatasetLoadOhlcv        = "cmd.analitic.dataset.load_ohlcv";
    public const string AnaliticDatasetRecomputeFeatures = "cmd.analitic.dataset.recompute_features";

    // ── Analytics (train / model) ─────────────────────────────────────────────
    public const string AnalyticsTrainStart  = "cmd.analytics.train.start";
    public const string AnalyticsTrainStatus = "cmd.analytics.train.status";
    public const string AnalyticsModelList   = "cmd.analytics.model.list";
    public const string AnalyticsModelLoad   = "cmd.analytics.model.load";
    public const string AnalyticsPredict     = "cmd.analytics.predict";
}
