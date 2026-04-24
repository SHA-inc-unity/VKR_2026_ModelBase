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

    // ── Events (fire-and-forget, no correlation round-trip) ──────────────
    public const string EvtDataIngestProgress     = "events.data.ingest.progress";

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
    ];
}
