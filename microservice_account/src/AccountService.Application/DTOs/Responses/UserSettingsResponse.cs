namespace AccountService.Application.DTOs.Responses;

public sealed record UserSettingsResponse(
    string Theme,
    string Locale,
    bool NotificationsEnabled,
    DateTimeOffset UpdatedAt
);
