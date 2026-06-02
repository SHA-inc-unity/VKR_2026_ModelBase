using SocialService.Domain.Entities;

namespace SocialService.Application.Interfaces.Repositories;

public interface IFavoriteRepository
{
    Task<IReadOnlyList<string>> GetSymbolsAsync(Guid userId, CancellationToken ct);
    Task<bool> ExistsAsync(Guid userId, string symbol, CancellationToken ct);
    Task AddAsync(Favorite favorite, CancellationToken ct);
    Task<bool> RemoveAsync(Guid userId, string symbol, CancellationToken ct);
    Task<IReadOnlyList<Guid>> GetUsersBySymbolAsync(string symbol, CancellationToken ct);

    /// <summary>
    /// Every distinct favorited symbol across all users. Used by the
    /// notification price-drift watcher to track exactly the symbols people
    /// actually care about (instead of a hard-coded list).
    /// </summary>
    Task<IReadOnlyList<string>> GetAllDistinctSymbolsAsync(CancellationToken ct);
}
