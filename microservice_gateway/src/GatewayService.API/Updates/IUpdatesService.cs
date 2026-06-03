namespace GatewayService.API.Updates;

/// <summary>
/// Fetches the app changelog ("releases") from microservice_data over Kafka
/// request/reply (<c>cmd.data.updates.list</c>) for the public GET /api/updates
/// endpoint. The gateway passes the data-service reply through verbatim.
/// </summary>
public interface IUpdatesService
{
    /// <summary>
    /// Requests the changelog from data-service. On success carries the raw JSON
    /// of the reply (e.g. <c>{ "releases": [ ... ] }</c>) to be passed through
    /// to the client. On a downstream error/timeout returns a failed result.
    /// </summary>
    Task<UpdatesResult> GetUpdatesAsync(CancellationToken ct = default);
}

/// <summary>
/// Result of an updates fetch — a success flag plus the raw reply JSON to pass
/// through. Mirrors the Result pattern used by DataServiceClient.
/// </summary>
public readonly record struct UpdatesResult(bool Ok, string? Json)
{
    public static UpdatesResult Success(string json) => new(true, json);
    public static UpdatesResult Fail() => new(false, null);
}
