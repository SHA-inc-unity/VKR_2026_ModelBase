namespace DataService.API.Kafka;

/// <summary>
/// Handler for <c>cmd.data.updates.list</c> — serves the append-only in-app
/// changelog. Request payload is empty (<c>{}</c>); the reply is
/// <c>{ "releases": [ ... ] }</c> (or <c>{ "error": "..." }</c> on failure),
/// newest release first, matching the contract the gateway/client depend on.
/// </summary>
public sealed partial class KafkaConsumerService
{
    private async Task<object> HandleUpdatesListAsync(CancellationToken ct)
    {
        var releases = await _appUpdatesRepo.ListAsync(ct);
        return new { releases };
    }
}
