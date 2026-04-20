namespace GatewayService.API.DTOs;

/// <summary>
/// Unified error contract returned by the gateway for all error responses.
/// </summary>
public sealed record ErrorResponse
{
    public int Status { get; init; }
    public string Title { get; init; } = string.Empty;
    public string? Detail { get; init; }
    public string? CorrelationId { get; init; }
    public DateTimeOffset Timestamp { get; init; } = DateTimeOffset.UtcNow;

    public static ErrorResponse ServiceUnavailable(string service, string? correlationId = null) => new()
    {
        Status = 503,
        Title = "Service Unavailable",
        Detail = $"The '{service}' service is temporarily unavailable.",
        CorrelationId = correlationId
    };

    public static ErrorResponse Unauthorized(string? correlationId = null) => new()
    {
        Status = 401,
        Title = "Unauthorized",
        Detail = "Authentication is required.",
        CorrelationId = correlationId
    };

    public static ErrorResponse InternalError(string? correlationId = null) => new()
    {
        Status = 500,
        Title = "Internal Server Error",
        Detail = "An unexpected error occurred.",
        CorrelationId = correlationId
    };
}
