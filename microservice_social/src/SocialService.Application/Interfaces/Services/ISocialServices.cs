using SocialService.Application.DTOs.Requests;
using SocialService.Application.DTOs.Responses;
using SocialService.Application.Interfaces.Repositories;

namespace SocialService.Application.Interfaces.Services;

public interface IFavoritesAppService
{
    Task<FavoritesResponse> ListAsync(Guid userId, CancellationToken ct);
    Task<bool> AddAsync(Guid userId, string symbol, CancellationToken ct);
    Task<bool> RemoveAsync(Guid userId, string symbol, CancellationToken ct);
    Task<IReadOnlyList<Guid>> UsersBySymbolAsync(string symbol, CancellationToken ct);

    /// All distinct favorited symbols across users (for the notification watcher).
    Task<IReadOnlyList<string>> AllFavoritedSymbolsAsync(CancellationToken ct);
}

public interface ICommentsAppService
{
    Task<CommentListResponse> ListAsync(
        string targetType,
        string targetId,
        int page,
        int pageSize,
        CommentSortMode sort,
        Guid? viewerUserId,
        CancellationToken ct);

    Task<CommentResponse> CreateAsync(Guid userId, CreateCommentRequest request, CancellationToken ct);
    Task<CommentResponse> UpdateAsync(Guid userId, Guid commentId, UpdateCommentRequest request, bool isAdmin, CancellationToken ct);
    Task DeleteAsync(Guid userId, Guid commentId, bool isAdmin, CancellationToken ct);
    Task<bool> LikeAsync(Guid userId, Guid commentId, CancellationToken ct);
    Task<bool> UnlikeAsync(Guid userId, Guid commentId, CancellationToken ct);
    Task<Guid?> GetAuthorAsync(Guid commentId, CancellationToken ct);
}

public interface IAssetSentimentAppService
{
    /// <summary>
    /// Aggregate sentiment for a target. <paramref name="viewerUserId"/> is
    /// optional (null for guests) and only drives <c>MyVote</c>.
    /// </summary>
    Task<SentimentResponse> GetAsync(string targetType, string targetId, Guid? viewerUserId, CancellationToken ct);

    /// <summary>
    /// Casts/moves/retracts the caller's vote (one row per (user, target)):
    /// "none" deletes, "bullish"/"bearish" upsert. Returns the fresh aggregate
    /// projected for the voter. Invalid vote → <see cref="ArgumentException"/> (400).
    /// </summary>
    Task<SentimentResponse> VoteAsync(Guid userId, string targetType, string targetId, string vote, CancellationToken ct);
}
