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

    public async Task<CommentListPage> ListAsync(string targetType, string targetId, int page, int pageSize, CommentSortMode sort, CancellationToken ct)
    {
        var q = _db.Comments.AsNoTracking()
            .Where(c => c.TargetType == targetType && c.TargetId == targetId);
        var total = await q.CountAsync(ct);

        // Reddit-style ordering. For top/top24h we use a correlated subquery
        // over CommentLikes so the page slice is selected by like-count rather
        // than by createdAt — EF Core translates the Count(...) lambda into a
        // SQL subselect. Replies (parentId != null) are NOT excluded from the
        // page slice itself because the UI groups them client-side under their
        // root parents and the page-size budget covers both root+reply rows.
        IOrderedQueryable<Comment> ordered;
        switch (sort)
        {
            case CommentSortMode.New:
                ordered = q.OrderByDescending(c => c.CreatedAt);
                break;
            case CommentSortMode.Top:
                ordered = q
                    .OrderByDescending(c => _db.CommentLikes.Count(l => l.CommentId == c.Id))
                    .ThenByDescending(c => c.CreatedAt);
                break;
            case CommentSortMode.Top24h:
            default:
                var cutoff = DateTime.UtcNow.AddHours(-24);
                ordered = q
                    .OrderByDescending(c => _db.CommentLikes.Count(l => l.CommentId == c.Id && l.CreatedAt >= cutoff))
                    .ThenByDescending(c => c.CreatedAt);
                break;
        }

        var items = await ordered
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
        try
        {
            await _db.SaveChangesAsync(ct);
        }
        catch (DbUpdateException ex) when (DbExceptions.IsUniqueViolation(ex))
        {
            // A concurrent like for the same (comment, user) won the race against
            // the prior Exists check. The like already exists, so treat as a no-op.
            _db.Entry(like).State = EntityState.Detached;
        }
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
