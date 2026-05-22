namespace AccountService.Application.DTOs.Responses;

public sealed record UserProfileResponse(
    Guid Id,
    string Email,
    string Username,
    string Status,
    IReadOnlyList<string> Roles,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt
);
