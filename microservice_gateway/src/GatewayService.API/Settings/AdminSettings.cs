namespace GatewayService.API.Settings;

/// <summary>
/// Settings for the admin backend facade.
/// SharedToken — the shared secret that admin-host must present in the
/// Authorization: Bearer &lt;token&gt; or X-Admin-Api-Key header for every
/// /api/admin/* request.  Leave empty to allow unauthenticated access
/// (only for local / full-stack development; never in production).
/// </summary>
public sealed class AdminSettings
{
    public string SharedToken { get; init; } = string.Empty;

    /// <summary>Default Kafka timeout for admin requests (seconds).</summary>
    public int DefaultTimeoutSeconds { get; init; } = 15;

    /// <summary>Timeout for long-running operations (export, ingest, detect anomalies, etc.).</summary>
    public int LongTimeoutSeconds { get; init; } = 300;
}
