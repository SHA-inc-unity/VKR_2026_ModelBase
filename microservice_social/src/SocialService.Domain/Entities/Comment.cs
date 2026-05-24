namespace SocialService.Domain.Entities;

public sealed class Comment
{
    public Guid Id { get; private set; }
    public Guid UserId { get; private set; }
    public string TargetType { get; private set; } = string.Empty;
    public string TargetId { get; private set; } = string.Empty;
    public Guid? ParentId { get; private set; }
    public string Body { get; private set; } = string.Empty;
    public DateTime CreatedAt { get; private set; }
    public DateTime UpdatedAt { get; private set; }
    public DateTime? DeletedAt { get; private set; }

    private Comment() { }

    public static Comment Create(Guid userId, string targetType, string targetId, string body, Guid? parentId)
    {
        var now = DateTime.UtcNow;
        return new Comment
        {
            Id = Guid.NewGuid(),
            UserId = userId,
            TargetType = NormalizeTargetType(targetType),
            TargetId = targetId.Trim(),
            ParentId = parentId,
            Body = body.Trim(),
            CreatedAt = now,
            UpdatedAt = now,
            DeletedAt = null,
        };
    }

    public void UpdateBody(string body)
    {
        Body = body.Trim();
        UpdatedAt = DateTime.UtcNow;
    }

    public void SoftDelete()
    {
        DeletedAt = DateTime.UtcNow;
        UpdatedAt = DateTime.UtcNow;
        // Preserve original body for moderation; UI suppresses display.
    }

    public bool IsDeleted => DeletedAt.HasValue;

    public static string NormalizeTargetType(string targetType)
    {
        var t = targetType.Trim().ToLowerInvariant();
        return t switch
        {
            "asset" => "asset",
            "news" => "news",
            _ => throw new ArgumentException($"Unsupported targetType '{targetType}'", nameof(targetType)),
        };
    }

    public static class TargetTypes
    {
        public const string Asset = "asset";
        public const string News = "news";
    }
}
