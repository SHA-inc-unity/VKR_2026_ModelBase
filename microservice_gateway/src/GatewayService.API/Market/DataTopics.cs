namespace GatewayService.API.Market;

/// <summary>
/// Kafka topic constants for the data-service commands used by the market API.
/// Kept in sync with microservice_data's Topics.cs.
/// </summary>
public static class DataTopics
{
    public const string CmdDataDatasetCoverage = "cmd.data.dataset.coverage";
    public const string CmdDataDatasetLatestRows = "cmd.data.dataset.latest_rows";
    public const string CmdDataDatasetRows     = "cmd.data.dataset.rows";
    public const string CmdDataDatasetIngest   = "cmd.data.dataset.ingest";
    public const string CmdDataDatasetJobsStart = "cmd.data.dataset.jobs.start";
    public const string CmdDataDatasetJobsGet   = "cmd.data.dataset.jobs.get";
    public const string CmdDataMarketWatcherRows = "cmd.data.market_watcher.rows";
    public const string CmdDataMarketWatcherTracked = "cmd.data.market_watcher.tracked_symbols";
    // Currency pairs center (single source of truth) — base/quote vocabularies + cross-product symbols.
    public const string CmdDataPairsList = "cmd.data.pairs.list";
}
