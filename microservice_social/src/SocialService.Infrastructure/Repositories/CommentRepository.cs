using Microsoft.EntityFrameworkCore;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Domain.Entities;
using SocialService.Infrastructure.Data;

namespace SocialService.Infrastructure.Repositories;

public sealed class CommentRepository : ICommentRepository
{
    private readonly SocialDbContext _db;
    public CommentRepository(SocialDbContext db) => _db = db;

    public Task<Comment?> GetByIdAsync(Guid id, CancellationToken ct) =>
        _db.Comments.FirstOrDefaultAsync(c => c.Id == id, ct);

    public async Task<CommentListPage> ListAsync(string targetType, string targetId, int page, int pageSize, CancellationToken ct)
    {
        var q = _db.Comments.AsNoTracking()
            .Where(c => c.TargetType == targetType && c.TargetId == targetId);
        var total = await q.CountAsync(ct);
        var items = await q
            .OrderBy(c => c.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(ct);
        return new CommentListPage { Items = items, Total = total };
    }

    public async Task AddAsync(Comment comment, CancellationToken ct)
    {
        await _db.Comments.AddAsync(comment, ct);
        await _db.SaveChangesAsync(ct);
    }

    public async Task UpdateAsync(Comment comment, CancellationToken ct)
    {
        _db.Comments.Update(comment);
        await _db.SaveChangesAsync(ct);
    }

    public async Task<IReadOnlyDictionary<Guid, int>> CountLikesAsync(IReadOnlyCollection<Guid> commentIds, CancellationToken ct)
    {
        if (commentIds.Count == 0) return new Dictionary<Guid, int>();
        var ids = commentIds.ToArray();
        return await _db.CommentLikes.AsNoTracking()
            .Where(l => ids.Contains(l.CommentId))
            .GroupBy(l => l.CommentId)
            .Select(g => new { g.Key, Count = g.Count() })
            .ToDictionaryAsync(x => x.Key, x => x.Count, ct);
    }

    public async Task<IReadOnlyDictionary<Guid, int>> CountRepliesAsync(IReadOnlyCollection<Guid> parentIds, CancellationToken ct)
    {
        if (parentIds.Count == 0) return new Dictionary<Guid, int>();
        var ids = parentIds.ToArray();
        return await _db.Comments.AsNoTracking()
            .Where(c => c.ParentId != null && ids.Contains(c.ParentId.Value))
            .GroupBy(c => c.ParentId!.Value)
            .Select(g => new { g.Key, Count = g.Count() })
            .ToDictionaryAsync(x => x.Key, x => x.Count, ct);
    }

    public async Task<IReadOnlySet<Guid>> WhichLikedByAsync(IReadOnlyCollection<Guid> commentIds, Guid userId, CancellationToken ct)
    {
        if (commentIds.Count == 0) return new HashSet<Guid>();
        var ids = commentIds.ToArray();
        var liked = await _db.CommentLikes.AsNoTracking()
            .Where(l => l.UserId == userId && ids.Contains(l.CommentId))
            .Select(l => l.CommentId)
            .ToListAsync(ct);
        return new HashSet<Guid>(liked);
    }
}

public sealed class CommentLikeRepository : ICommentLikeRepository
{
    private readonly SocialDbContext _db;
    public CommentLikeRepository(SocialDbContext db) => _db = db;

    public Task<bool> ExistsAsync(Guid commentId, Guid userId, CancellationToken ct) =>
        _db.CommentLikes.AsNoTracking().AnyAsync(
            l => l.CommentId == commentId && l.UserId == userId, ct);

    public async Task AddAsync(CommentLike like, CancellationToken ct)
    {
        await _db.CommentLikes.AddAsync(like, ct);
        await _db.SaveChangesAsync(ct);
    }

    public async Task<bool> RemoveAsync(Guid commentId, Guid userId, CancellationToken ct)
    {
        var entity = await _db.CommentLikes.FirstOrDefaultAsync(
            l => l.CommentId == commentId && l.UserId == userId, ct);
        if (entity is null) return false;
        _db.CommentLikes.Remove(entity);
        await _db.SaveChangesAsync(ct);
        return true;
    }
}
