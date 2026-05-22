namespace AccountService.Application.DTOs.Requests;

public sealed record UpdateSettingsRequest(
    string? Theme = null,
    string? Locale = null,
    bool? NotificationsEnabled = null
);
