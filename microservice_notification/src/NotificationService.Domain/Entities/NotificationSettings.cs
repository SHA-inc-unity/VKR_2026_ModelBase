namespace NotificationService.Domain.Entities;

public sealed class NotificationSettings
{
    public Guid UserId { get; private set; }
    public bool EnableReply { get; private set; } = true;
    public bool EnableNews { get; private set; } = true;
    public bool EnablePrice { get; private set; } = true;
    public decimal PriceThresholdPct { get; private set; } = 5m;
    public DateTime UpdatedAt { get; private set; }

    private NotificationSettings() { }

    public static NotificationSettings Default(Guid userId) => new()
    {
        UserId = userId,
        EnableReply = true,
        EnableNews = true,
        EnablePrice = true,
        PriceThresholdPct = 5m,
        UpdatedAt = DateTime.UtcNow,
    };

    public void Update(bool? enableReply, bool? enableNews, bool? enablePrice, decimal? priceThresholdPct)
    {
        if (enableReply.HasValue) EnableReply = enableReply.Value;
        if (enableNews.HasValue) EnableNews = enableNews.Value;
        if (enablePrice.HasValue) EnablePrice = enablePrice.Value;
        if (priceThresholdPct.HasValue)
        {
            if (priceThresholdPct.Value < 0.0001m) priceThresholdPct = 0.0001m;
            if (priceThresholdPct.Value > 100m) priceThresholdPct = 100m;
            PriceThresholdPct = priceThresholdPct.Value;
        }
        UpdatedAt = DateTime.UtcNow;
    }
}
