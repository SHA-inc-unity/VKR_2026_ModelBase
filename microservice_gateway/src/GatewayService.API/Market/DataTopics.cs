namespace GatewayService.API.Market;

/// <summary>
/// Kafka topic constants for the data-service commands used by the market API.
/// Kept in sync with microservice_data's Topics.cs.
/// </summary>
public static class DataTopics
{
    public const string CmdDataDatasetCoverage = "cmd.data.dataset.coverage";
    public const string CmdDataDatasetRows     = "cmd.data.dataset.rows";
    public const string CmdDataDatasetIngest   = "cmd.data.dataset.ingest";
}
