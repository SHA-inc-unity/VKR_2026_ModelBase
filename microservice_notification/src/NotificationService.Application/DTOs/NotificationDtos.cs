namespace NotificationService.Application.DTOs;

public sealed class NotificationResponse
{
    public Guid Id { get; set; }
    public string Kind { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
    public string Body { get; set; } = string.Empty;
    public string? Deeplink { get; set; }
    public DateTime CreatedAt { get; set; }
    public DateTime? ReadAt { get; set; }
}

public sealed class NotificationListResponse
{
    public IReadOnlyList<NotificationResponse> Items { get; set; } = Array.Empty<NotificationResponse>();
    public int Total { get; set; }
    public int Unread { get; set; }
    public int Page { get; set; }
    public int PageSize { get; set; }
}

public sealed class UnreadCountResponse
{
    public int Unread { get; set; }
}

public sealed class NotificationSettingsResponse
{
    public bool EnableReply { get; set; }
    public bool EnableNews { get; set; }
    public bool EnablePrice { get; set; }
    public decimal PriceThresholdPct { get; set; }
}

public sealed class UpdateNotificationSettingsRequest
{
    public bool? EnableReply { get; set; }
    public bool? EnableNews { get; set; }
    public bool? EnablePrice { get; set; }
    public decimal? PriceThresholdPct { get; set; }
}
