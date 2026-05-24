using SocialService.Application.DTOs.Requests;
using SocialService.Application.DTOs.Responses;

namespace SocialService.Application.Interfaces.Services;

public interface IFavoritesAppService
{
    Task<FavoritesResponse> ListAsync(Guid userId, CancellationToken ct);
    Task<bool> AddAsync(Guid userId, string symbol, CancellationToken ct);
    Task<bool> RemoveAsync(Guid userId, string symbol, CancellationToken ct);
    Task<IReadOnlyList<Guid>> UsersBySymbolAsync(string symbol, CancellationToken ct);
}

public interface ICommentsAppService
{
    Task<CommentListResponse> ListAsync(
        string targetType,
        string targetId,
        int page,
        int pageSize,
        Guid? viewerUserId,
        CancellationToken ct);

    Task<CommentResponse> CreateAsync(Guid userId, CreateCommentRequest request, CancellationToken ct);
    Task<CommentResponse> UpdateAsync(Guid userId, Guid commentId, UpdateCommentRequest request, bool isAdmin, CancellationToken ct);
    Task DeleteAsync(Guid userId, Guid commentId, bool isAdmin, CancellationToken ct);
    Task<bool> LikeAsync(Guid userId, Guid commentId, CancellationToken ct);
    Task<bool> UnlikeAsync(Guid userId, Guid commentId, CancellationToken ct);
    Task<Guid?> GetAuthorAsync(Guid commentId, CancellationToken ct);
}
