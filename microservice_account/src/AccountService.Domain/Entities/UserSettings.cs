namespace AccountService.Domain.Entities;

public class UserSettings
{
    public Guid UserId { get; private set; }
    public string Theme { get; private set; } = "system";
    public string Locale { get; private set; } = "en";
    public bool NotificationsEnabled { get; private set; } = true;
    public DateTimeOffset CreatedAt { get; private set; }
    public DateTimeOffset UpdatedAt { get; private set; }

    public User User { get; private set; } = null!;

    private UserSettings() { }

    public static UserSettings CreateDefault(Guid userId)
    {
        var now = DateTimeOffset.UtcNow;
        return new UserSettings
        {
            UserId = userId,
            Theme = "system",
            Locale = "en",
            NotificationsEnabled = true,
            CreatedAt = now,
            UpdatedAt = now
        };
    }

    public void Update(string? theme, string? locale, bool? notificationsEnabled)
    {
        if (!string.IsNullOrWhiteSpace(theme)) Theme = theme;
        if (!string.IsNullOrWhiteSpace(locale)) Locale = locale;
        if (notificationsEnabled.HasValue) NotificationsEnabled = notificationsEnabled.Value;
        UpdatedAt = DateTimeOffset.UtcNow;
    }
}
