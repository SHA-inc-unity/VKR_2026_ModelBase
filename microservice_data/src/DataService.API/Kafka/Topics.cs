namespace DataService.API.Kafka;

public static class Topics
{
    public const string CmdDataHealth             = "cmd.data.health";
    public const string CmdDataDbPing             = "cmd.data.db.ping";
    public const string CmdDataDatasetListTables  = "cmd.data.dataset.list_tables";
    public const string CmdDataDatasetCoverage    = "cmd.data.dataset.coverage";
    public const string CmdDataDatasetTimestamps  = "cmd.data.dataset.timestamps";
    public const string CmdDataDatasetMissing     = "cmd.data.dataset.find_missing";
    public const string CmdDataDatasetRows        = "cmd.data.dataset.rows";
    public const string CmdDataDatasetExport      = "cmd.data.dataset.export";
    public const string CmdDataDatasetExportFull  = "cmd.data.dataset.export_full";
    public const string CmdDataDatasetSchema      = "cmd.data.dataset.table_schema";
    public const string CmdDataDatasetNormalizeTf = "cmd.data.dataset.normalize_timeframe";
    public const string CmdDataDatasetMakeTable   = "cmd.data.dataset.make_table_name";
    public const string CmdDataDatasetInstrument  = "cmd.data.dataset.instrument_details";
    public const string CmdDataDatasetConstants   = "cmd.data.dataset.constants";
    public const string CmdDataDatasetIngest      = "cmd.data.dataset.ingest";
    public const string CmdDataDatasetDeleteRows  = "cmd.data.dataset.delete_rows";
    public const string CmdDataDatasetImportCsv   = "cmd.data.dataset.import_csv";
    public const string CmdDataDatasetColumnStats     = "cmd.data.dataset.column_stats";
    public const string CmdDataDatasetColumnHistogram = "cmd.data.dataset.column_histogram";
    public const string CmdDataDatasetBrowse          = "cmd.data.dataset.browse";
    public const string CmdDataDatasetComputeFeatures = "cmd.data.dataset.compute_features";
    public const string CmdDataDatasetDetectAnomalies = "cmd.data.dataset.detect_anomalies";
    public const string CmdDataDatasetCleanPreview    = "cmd.data.dataset.clean.preview";
    public const string CmdDataDatasetCleanApply      = "cmd.data.dataset.clean.apply";
    public const string CmdDataDatasetAuditLog        = "cmd.data.dataset.audit_log";
    public const string CmdDataDatasetUpsertOhlcv     = "cmd.data.dataset.upsert_ohlcv";

    // ── Background-job control plane (Phase A of jobs redesign) ──────────
    // Replaces ad-hoc blocking req/reply for long-running ops. Heavy
    // handlers (ingest, detect_anomalies, compute_features, clean.apply,
    // export, import_csv, upsert_ohlcv) will be migrated to operate on
    // dataset_jobs rows in subsequent phases; the Phase-A handlers below
    // already accept jobs.start/get/cancel/list and persist records, but
    // the runner that actually executes them is Phase B.
    public const string CmdDataDatasetJobsStart  = "cmd.data.dataset.jobs.start";
    public const string CmdDataDatasetJobsCancel = "cmd.data.dataset.jobs.cancel";
    public const string CmdDataDatasetJobsGet    = "cmd.data.dataset.jobs.get";
    public const string CmdDataDatasetJobsList   = "cmd.data.dataset.jobs.list";

    // ── Events (fire-and-forget, no correlation round-trip) ──────────────
    public const string EvtDataIngestProgress         = "events.data.ingest.progress";
    public const string EvtDataDatasetJobProgress     = "events.data.dataset.job.progress";
    public const string EvtDataDatasetJobCompleted    = "events.data.dataset.job.completed";

    public static readonly string[] AllConsumed =
    [
        CmdDataHealth,
        CmdDataDbPing,
        CmdDataDatasetListTables,
        CmdDataDatasetCoverage,
        CmdDataDatasetTimestamps,
        CmdDataDatasetMissing,
        CmdDataDatasetRows,
        CmdDataDatasetExport,
        CmdDataDatasetExportFull,
        CmdDataDatasetSchema,
        CmdDataDatasetNormalizeTf,
        CmdDataDatasetMakeTable,
        CmdDataDatasetInstrument,
        CmdDataDatasetConstants,
        CmdDataDatasetIngest,
        CmdDataDatasetDeleteRows,
        CmdDataDatasetImportCsv,
        CmdDataDatasetColumnStats,
        CmdDataDatasetColumnHistogram,
        CmdDataDatasetBrowse,
        CmdDataDatasetComputeFeatures,
        CmdDataDatasetDetectAnomalies,
        CmdDataDatasetCleanPreview,
        CmdDataDatasetCleanApply,
        CmdDataDatasetAuditLog,
        CmdDataDatasetUpsertOhlcv,
        CmdDataDatasetJobsStart,
        CmdDataDatasetJobsCancel,
        CmdDataDatasetJobsGet,
        CmdDataDatasetJobsList,
    ];
}
