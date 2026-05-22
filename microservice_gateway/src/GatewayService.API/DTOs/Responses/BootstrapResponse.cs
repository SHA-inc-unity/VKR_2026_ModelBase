namespace GatewayService.API.DTOs.Responses;

/// <summary>
/// Aggregated bootstrap response — called ONCE on app launch by Flutter.
/// Contains everything the client needs to render the initial state.
/// </summary>
public sealed record BootstrapResponse
{
    /// <summary>Current authenticated user summary. Null if request is unauthenticated.</summary>
    public UserSummaryDto? User { get; init; }

    public FeatureFlagsDto FeatureFlags { get; init; } = new();

    public SystemStatusDto SystemStatus { get; init; } = new();

    /// <summary>API contract version for the client to detect breaking changes.</summary>
    public string ApiVersion { get; init; } = "1.0";

    public DateTimeOffset GeneratedAt { get; init; } = DateTimeOffset.UtcNow;

    /// <summary>List of service names that failed during aggregation. Client can render gracefully.</summary>
    public IReadOnlyList<string> DegradedServices { get; init; } = [];
}

public sealed record UserSummaryDto
{
    public Guid Id { get; init; }
    public string Email { get; init; } = string.Empty;
    public string Username { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    public IReadOnlyList<string> Roles { get; init; } = [];
    public DateTimeOffset CreatedAt { get; init; }
}

public sealed record FeatureFlagsDto
{
    public bool Portfolio { get; init; }
    public bool Market { get; init; }
    public bool News { get; init; }
    public bool Notifications { get; init; }
}

public sealed record SystemStatusDto
{
    /// <summary>"operational" | "degraded" | "outage"</summary>
    public string Status { get; init; } = "operational";
    public IReadOnlyDictionary<string, string> Services { get; init; } = new Dictionary<string, string>();
}
