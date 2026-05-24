using SocialService.Domain.Entities;

namespace SocialService.Application.Interfaces.Repositories;

public sealed class CommentListPage
{
    public IReadOnlyList<Comment> Items { get; init; } = Array.Empty<Comment>();
    public int Total { get; init; }
}

public interface ICommentRepository
{
    Task<Comment?> GetByIdAsync(Guid id, CancellationToken ct);
    Task<CommentListPage> ListAsync(string targetType, string targetId, int page, int pageSize, CancellationToken ct);
    Task AddAsync(Comment comment, CancellationToken ct);
    Task UpdateAsync(Comment comment, CancellationToken ct);
    Task<IReadOnlyDictionary<Guid, int>> CountLikesAsync(IReadOnlyCollection<Guid> commentIds, CancellationToken ct);
    Task<IReadOnlyDictionary<Guid, int>> CountRepliesAsync(IReadOnlyCollection<Guid> parentIds, CancellationToken ct);
    Task<IReadOnlySet<Guid>> WhichLikedByAsync(IReadOnlyCollection<Guid> commentIds, Guid userId, CancellationToken ct);
}

public interface ICommentLikeRepository
{
    Task<bool> ExistsAsync(Guid commentId, Guid userId, CancellationToken ct);
    Task AddAsync(CommentLike like, CancellationToken ct);
    Task<bool> RemoveAsync(Guid commentId, Guid userId, CancellationToken ct);
}
