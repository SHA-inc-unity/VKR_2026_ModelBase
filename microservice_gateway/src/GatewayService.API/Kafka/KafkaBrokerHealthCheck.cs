using Confluent.Kafka;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Kafka;

/// <summary>
/// Readiness probe for the Kafka bootstrap listener used by gateway request/reply.
/// Verifies broker metadata over the same bootstrap address as the producer/consumer.
/// </summary>
public sealed class KafkaBrokerHealthCheck : IHealthCheck
{
    private static readonly TimeSpan ProbeTimeout = TimeSpan.FromSeconds(2);

    private readonly string _bootstrapServers;

    public KafkaBrokerHealthCheck(IOptions<KafkaSettings> options)
    {
        _bootstrapServers = options.Value.BootstrapServers;
    }

    public Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context,
        CancellationToken cancellationToken = default)
    {
        try
        {
            using var admin = new AdminClientBuilder(new AdminClientConfig
            {
                BootstrapServers = _bootstrapServers,
                SocketTimeoutMs = (int)ProbeTimeout.TotalMilliseconds,
            }).Build();

            var metadata = admin.GetMetadata(ProbeTimeout);
            if (metadata.Brokers.Count == 0)
            {
                return Task.FromResult(HealthCheckResult.Unhealthy(
                    $"Kafka bootstrap '{_bootstrapServers}' returned no brokers."));
            }

            return Task.FromResult(HealthCheckResult.Healthy(
                $"Kafka bootstrap '{_bootstrapServers}' is reachable."));
        }
        catch (Exception ex)
        {
            return Task.FromResult(HealthCheckResult.Unhealthy(
                $"Kafka bootstrap '{_bootstrapServers}' is unreachable: {ex.Message}", ex));
        }
    }
}