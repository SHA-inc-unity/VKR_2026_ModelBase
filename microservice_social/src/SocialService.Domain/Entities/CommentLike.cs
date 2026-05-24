namespace SocialService.Domain.Entities;

public sealed class CommentLike
{
    public Guid CommentId { get; private set; }
    public Guid UserId { get; private set; }
    public DateTime CreatedAt { get; private set; }

    private CommentLike() { }

    public static CommentLike Create(Guid commentId, Guid userId)
    {
        return new CommentLike
        {
            CommentId = commentId,
            UserId = userId,
            CreatedAt = DateTime.UtcNow,
        };
    }
}
