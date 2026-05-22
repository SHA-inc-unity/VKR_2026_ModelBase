namespace AccountService.Application.DTOs.Responses;

public sealed record AuthResponse(
    string AccessToken,
    string RefreshToken,
    DateTimeOffset AccessTokenExpiresAt,
    DateTimeOffset RefreshTokenExpiresAt,
    Guid Uid,
    string AccountType,
    IReadOnlyList<string> Roles,
    UserProfileResponse User
);
