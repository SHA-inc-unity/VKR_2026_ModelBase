namespace GatewayService.API.Settings;

public sealed class ResilienceSettings
{
    public const string SectionName = "Resilience";

    public int TimeoutSeconds { get; init; } = 5;
    public int RetryCount { get; init; } = 2;
    public int CircuitBreakerFailureThreshold { get; init; } = 5;
    public int CircuitBreakerDurationSeconds { get; init; } = 30;
}
