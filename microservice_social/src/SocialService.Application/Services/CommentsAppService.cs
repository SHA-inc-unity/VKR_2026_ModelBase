using SocialService.Application.Common.Exceptions;
using SocialService.Application.DTOs.Requests;
using SocialService.Application.DTOs.Responses;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Application.Interfaces.Services;
using SocialService.Domain.Entities;

namespace SocialService.Application.Services;

public sealed class CommentsAppService : ICommentsAppService
{
    private readonly ICommentRepository _comments;
    private readonly ICommentLikeRepository _likes;
    private readonly IUserDirectoryService _users;
    private readonly IEventBus _bus;

    public CommentsAppService(
        ICommentRepository comments,
        ICommentLikeRepository likes,
        IUserDirectoryService users,
        IEventBus bus)
    {
        _comments = comments;
        _likes = likes;
        _users = users;
        _bus = bus;
    }

    public async Task<CommentListResponse> ListAsync(
        string targetType,
        string targetId,
        int page,
        int pageSize,
        Guid? viewerUserId,
        CancellationToken ct)
    {
        targetType = Comment.NormalizeTargetType(targetType);
        if (string.IsNullOrWhiteSpace(targetId))
            throw new InvalidCommentTargetException("targetId is required");

        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 50;
        if (pageSize > 200) pageSize = 200;

        var slice = await _comments.ListAsync(targetType, targetId, page, pageSize, ct);
        if (slice.Items.Count == 0)
        {
            return new CommentListResponse { Items = Array.Empty<CommentResponse>(), Total = slice.Total, Page = page, PageSize = pageSize };
        }

        var ids = slice.Items.Select(c => c.Id).ToList();
        var authorIds = slice.Items.Select(c => c.UserId).Distinct().ToList();

        var likeCounts = await _comments.CountLikesAsync(ids, ct);
        var replyCounts = await _comments.CountRepliesAsync(ids, ct);
        var likedByMe = viewerUserId.HasValue
            ? await _comments.WhichLikedByAsync(ids, viewerUserId.Value, ct)
            : (IReadOnlySet<Guid>)new HashSet<Guid>();
        var authors = await _users.ResolveAsync(authorIds, ct);

        var items = slice.Items.Select(c => new CommentResponse
        {
            Id = c.Id,
            Author = authors.TryGetValue(c.UserId, out var u)
                ? new CommentAuthorDto { Id = u.Id, Username = u.Username }
                : new CommentAuthorDto { Id = c.UserId, Username = "unknown" },
            TargetType = c.TargetType,
            TargetId = c.TargetId,
            ParentId = c.ParentId,
            Body = c.IsDeleted ? string.Empty : c.Body,
            CreatedAt = c.CreatedAt,
            UpdatedAt = c.UpdatedAt,
            Deleted = c.IsDeleted,
            LikeCount = likeCounts.TryGetValue(c.Id, out var lc) ? lc : 0,
            LikedByMe = likedByMe.Contains(c.Id),
            ReplyCount = replyCounts.TryGetValue(c.Id, out var rc) ? rc : 0,
        }).ToList();

        return new CommentListResponse
        {
            Items = items,
            Total = slice.Total,
            Page = page,
            PageSize = pageSize,
        };
    }

    public async Task<CommentResponse> CreateAsync(Guid userId, CreateCommentRequest request, CancellationToken ct)
    {
        var body = (request.Body ?? string.Empty).Trim();
        if (string.IsNullOrEmpty(body) || body.Length > 4000)
            throw new InvalidCommentTargetException("Body must be 1..4000 chars");

        var targetType = Comment.NormalizeTargetType(request.TargetType);
        var targetId = (request.TargetId ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(targetId))
            throw new InvalidCommentTargetException("targetId is required");

        // 1-level reply policy: if parent is itself a reply, lift parentId to its parent.
        Guid? parentId = request.ParentId;
        if (parentId.HasValue)
        {
            var parent = await _comments.GetByIdAsync(parentId.Value, ct)
                ?? throw new InvalidCommentTargetException("parent comment not found");
            if (parent.TargetType != targetType || parent.TargetId != targetId)
                throw new InvalidCommentTargetException("parent comment belongs to different target");
            if (parent.ParentId.HasValue) parentId = parent.ParentId;
        }

        var comment = Comment.Create(userId, targetType, targetId, body, parentId);
        await _comments.AddAsync(comment, ct);

        await _bus.PublishAsync("comment.created", new
        {
            commentId = comment.Id,
            targetType = comment.TargetType,
            targetId = comment.TargetId,
            parentId = comment.ParentId,
            authorId = comment.UserId,
        }, ct);

        var authors = await _users.ResolveAsync(new[] { comment.UserId }, ct);
        return new CommentResponse
        {
            Id = comment.Id,
            Author = authors.TryGetValue(comment.UserId, out var u)
                ? new CommentAuthorDto { Id = u.Id, Username = u.Username }
                : new CommentAuthorDto { Id = comment.UserId, Username = "unknown" },
            TargetType = comment.TargetType,
            TargetId = comment.TargetId,
            ParentId = comment.ParentId,
            Body = comment.Body,
            CreatedAt = comment.CreatedAt,
            UpdatedAt = comment.UpdatedAt,
            Deleted = false,
            LikeCount = 0,
            LikedByMe = false,
            ReplyCount = 0,
        };
    }

    public async Task<CommentResponse> UpdateAsync(Guid userId, Guid commentId, UpdateCommentRequest request, bool isAdmin, CancellationToken ct)
    {
        var comment = await _comments.GetByIdAsync(commentId, ct)
            ?? throw new CommentNotFoundException(commentId);
        if (comment.IsDeleted) throw new CommentNotFoundException(commentId);
        if (comment.UserId != userId && !isAdmin)
            throw new ForbiddenSocialActionException("Only the author or an admin can edit this comment");

        var body = (request.Body ?? string.Empty).Trim();
        if (string.IsNullOrEmpty(body) || body.Length > 4000)
            throw new InvalidCommentTargetException("Body must be 1..4000 chars");

        comment.UpdateBody(body);
        await _comments.UpdateAsync(comment, ct);

        var authors = await _users.ResolveAsync(new[] { comment.UserId }, ct);
        var likes = await _comments.CountLikesAsync(new[] { comment.Id }, ct);
        var replies = await _comments.CountRepliesAsync(new[] { comment.Id }, ct);
        return new CommentResponse
        {
            Id = comment.Id,
            Author = authors.TryGetValue(comment.UserId, out var u)
                ? new CommentAuthorDto { Id = u.Id, Username = u.Username }
                : new CommentAuthorDto { Id = comment.UserId, Username = "unknown" },
            TargetType = comment.TargetType,
            TargetId = comment.TargetId,
            ParentId = comment.ParentId,
            Body = comment.Body,
            CreatedAt = comment.CreatedAt,
            UpdatedAt = comment.UpdatedAt,
            Deleted = false,
            LikeCount = likes.TryGetValue(comment.Id, out var lc) ? lc : 0,
            LikedByMe = false,
            ReplyCount = replies.TryGetValue(comment.Id, out var rc) ? rc : 0,
        };
    }

    public async Task DeleteAsync(Guid userId, Guid commentId, bool isAdmin, CancellationToken ct)
    {
        var comment = await _comments.GetByIdAsync(commentId, ct)
            ?? throw new CommentNotFoundException(commentId);
        if (comment.IsDeleted) return;
        if (comment.UserId != userId && !isAdmin)
            throw new ForbiddenSocialActionException("Only the author or an admin can delete this comment");

        comment.SoftDelete();
        await _comments.UpdateAsync(comment, ct);
    }

    public async Task<bool> LikeAsync(Guid userId, Guid commentId, CancellationToken ct)
    {
        var comment = await _comments.GetByIdAsync(commentId, ct)
            ?? throw new CommentNotFoundException(commentId);
        if (comment.IsDeleted) throw new CommentNotFoundException(commentId);

        if (await _likes.ExistsAsync(commentId, userId, ct)) return false;
        await _likes.AddAsync(CommentLike.Create(commentId, userId), ct);

        await _bus.PublishAsync("comment.liked", new
        {
            commentId,
            authorId = comment.UserId,
            actorId = userId,
        }, ct);
        return true;
    }

    public Task<bool> UnlikeAsync(Guid userId, Guid commentId, CancellationToken ct) =>
        _likes.RemoveAsync(commentId, userId, ct);

    public async Task<Guid?> GetAuthorAsync(Guid commentId, CancellationToken ct)
    {
        var c = await _comments.GetByIdAsync(commentId, ct);
        return c?.UserId;
    }
}
