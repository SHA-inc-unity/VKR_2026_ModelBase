namespace GatewayService.API.DTOs.Responses;

public sealed record NotificationsResponse
{
    public IReadOnlyList<NotificationDto> Items { get; init; } = [];
    public int UnreadCount { get; init; }
    public bool Degraded { get; init; }
}

public sealed record NotificationDto
{
    public string Id { get; init; } = string.Empty;
    /// <summary>e.g. "price_alert", "system", "news"</summary>
    public string Type { get; init; } = string.Empty;
    public string Title { get; init; } = string.Empty;
    public string? Body { get; init; }
    public bool IsRead { get; init; }
    public DateTimeOffset CreatedAt { get; init; }
}
