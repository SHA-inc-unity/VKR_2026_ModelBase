namespace GatewayService.API.Settings;

/// <summary>
/// Settings for the admin backend facade.
/// /api/admin/* itself is authorized by a validated JWT with the `admin` role;
/// this class only controls request timeouts.
/// </summary>
public sealed class AdminSettings
{
    /// <summary>Default Kafka timeout for admin requests (seconds).</summary>
    public int DefaultTimeoutSeconds { get; init; } = 15;

    /// <summary>Timeout for long-running operations (export, ingest, detect anomalies, etc.).</summary>
    public int LongTimeoutSeconds { get; init; } = 300;
}
