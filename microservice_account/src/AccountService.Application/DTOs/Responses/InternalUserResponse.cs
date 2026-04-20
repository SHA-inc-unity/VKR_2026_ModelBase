namespace AccountService.Application.DTOs.Responses;

/// <summary>Response for internal inter-service calls (e.g. from API Gateway).</summary>
public sealed record InternalUserResponse(
    Guid Id,
    string Email,
    string Username,
    string Status,
    IReadOnlyList<string> Roles
);
