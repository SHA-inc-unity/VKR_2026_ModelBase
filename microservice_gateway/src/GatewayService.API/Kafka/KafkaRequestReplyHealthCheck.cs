using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace GatewayService.API.Kafka;

/// <summary>
/// Readiness probe for the gateway Kafka request/reply path.
/// Requires a live consumer assignment on the per-instance reply inbox.
/// </summary>
public sealed class KafkaRequestReplyHealthCheck : IHealthCheck
{
    private readonly IKafkaRequestClientProbe _probe;

    public KafkaRequestReplyHealthCheck(IKafkaRequestClientProbe probe)
    {
        _probe = probe;
    }

    public Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context,
        CancellationToken cancellationToken = default)
    {
        if (_probe.IsReplyInboxReady)
        {
            return Task.FromResult(HealthCheckResult.Healthy(
                $"Kafka reply inbox '{_probe.ReplyInbox}' is assigned and ready."));
        }

        return Task.FromResult(HealthCheckResult.Unhealthy(
            $"Kafka reply inbox '{_probe.ReplyInbox}' is not assigned yet."));
    }
}