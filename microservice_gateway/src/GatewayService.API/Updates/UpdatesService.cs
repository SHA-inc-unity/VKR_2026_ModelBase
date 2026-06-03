using System.Text.Json;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Updates;

/// <inheritdoc />
public sealed class UpdatesService : IUpdatesService
{
    private readonly IKafkaRequestClient _kafka;
    private readonly MarketSettings _settings;
    private readonly ILogger<UpdatesService> _log;

    public UpdatesService(
        IKafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<UpdatesService> log)
    {
        _kafka = kafka;
        _settings = settings.Value;
        _log = log;
    }

    /// <inheritdoc />
    public async Task<UpdatesResult> GetUpdatesAsync(CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);

        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataUpdatesList,
                new { },
                timeout,
                ct);

            if (reply.ValueKind == JsonValueKind.Object &&
                reply.TryGetProperty("error", out var errEl))
            {
                _log.LogWarning(
                    "data-service updates.list returned an error: {Error}",
                    errEl.ValueKind == JsonValueKind.String
                        ? errEl.GetString()
                        : errEl.ToString());
                return UpdatesResult.Fail();
            }

            // Pass the reply through verbatim (e.g. { "releases": [ ... ] }).
            return UpdatesResult.Success(reply.GetRawText());
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex, "Updates Kafka request timed out on {Topic}", DataTopics.CmdDataUpdatesList);
            return UpdatesResult.Fail();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Updates Kafka request failed on {Topic}", DataTopics.CmdDataUpdatesList);
            return UpdatesResult.Fail();
        }
    }
}
