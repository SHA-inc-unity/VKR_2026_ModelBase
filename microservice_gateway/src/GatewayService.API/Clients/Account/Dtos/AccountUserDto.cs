namespace GatewayService.API.Clients.Account.Dtos;

/// <summary>Maps to UserProfileResponse from Account Service.</summary>
public sealed record AccountUserDto
{
    public Guid Id { get; init; }
    public string Email { get; init; } = string.Empty;
    public string Username { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    public IReadOnlyList<string> Roles { get; init; } = [];
    public DateTimeOffset CreatedAt { get; init; }
    public DateTimeOffset UpdatedAt { get; init; }
}
