namespace NotificationService.Application.DTOs;

/// <summary>
/// Wire shape for a price alert — kept byte-for-byte compatible with the gateway's
/// historical <c>PriceAlertDto</c> JSON so the Flutter client is unaffected when the
/// gateway switches to forwarding CRUD here: <c>{ id, symbol, condition, targetPrice,
/// isEnabled, createdAt }</c>. <see cref="Id"/> is the alert's Guid in "N" form
/// (stable string id); <see cref="CreatedAt"/> is a UTC <see cref="DateTimeOffset"/>.
/// </summary>
public sealed class AlertResponse
{
    public string Id { get; set; } = string.Empty;
    public string Symbol { get; set; } = string.Empty;
    public string Condition { get; set; } = string.Empty;
    public decimal TargetPrice { get; set; }
    public bool IsEnabled { get; set; }
    public DateTimeOffset CreatedAt { get; set; }
}

/// <summary>Body for POST /api/alerts — mirrors the gateway's CreateAlertRequest.</summary>
public sealed class CreateAlertRequest
{
    public string Symbol { get; set; } = string.Empty;
    public string Condition { get; set; } = string.Empty;
    public decimal TargetPrice { get; set; }
    public bool IsEnabled { get; set; } = true;
}

/// <summary>Body for PATCH /api/alerts/{id} — partial update; null fields are left unchanged.</summary>
public sealed class UpdateAlertRequest
{
    public string? Symbol { get; set; }
    public string? Condition { get; set; }
    public decimal? TargetPrice { get; set; }
    public bool? IsEnabled { get; set; }
}
