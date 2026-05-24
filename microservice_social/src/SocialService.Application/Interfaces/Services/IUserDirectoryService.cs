namespace SocialService.Application.Interfaces.Services;

public sealed class UserSummary
{
    public Guid Id { get; init; }
    public string Username { get; init; } = string.Empty;
}

/// <summary>
/// Looks up minimal user info (id → username) for rendering comment author chips.
/// Implementation calls AccountService over HTTP (internal API).
/// </summary>
public interface IUserDirectoryService
{
    Task<IReadOnlyDictionary<Guid, UserSummary>> ResolveAsync(
        IReadOnlyCollection<Guid> userIds,
        CancellationToken ct);
}
