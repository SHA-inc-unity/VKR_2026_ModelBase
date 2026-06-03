namespace SocialService.Domain.Entities;

/// <summary>
/// One persistent bullish/bearish vote per (user, target). Mirrors
/// <see cref="CommentLike"/> but carries a direction, so a single row exists per
/// (UserId, TargetType, TargetId): re-voting <see cref="Change"/>s the existing
/// row, and retracting ("none") deletes it. There is NO daily reset — the vote
/// stands until the user changes or retracts it.
/// </summary>
public sealed class AssetSentiment
{
    public Guid UserId { get; private set; }
    public string TargetType { get; private set; } = string.Empty;
    public string TargetId { get; private set; } = string.Empty;
    public string Vote { get; private set; } = string.Empty;
    public DateTime CreatedAt { get; private set; }
    public DateTime UpdatedAt { get; private set; }

    private AssetSentiment() { }

    public static AssetSentiment Create(Guid userId, string targetType, string targetId, string vote)
    {
        var now = DateTime.UtcNow;
        return new AssetSentiment
        {
            UserId = userId,
            TargetType = Comment.NormalizeTargetType(targetType),
            TargetId = targetId.Trim(),
            Vote = NormalizeVote(vote),
            CreatedAt = now,
            UpdatedAt = now,
        };
    }

    public void Change(string vote)
    {
        Vote = NormalizeVote(vote);
        UpdatedAt = DateTime.UtcNow;
    }

    /// <summary>
    /// Validates a stored vote direction. Only the two persisted directions are
    /// accepted here; the retraction sentinel ("none") never reaches a row and
    /// is handled by the app service (it deletes instead of writing).
    /// </summary>
    public static string NormalizeVote(string vote)
    {
        var v = (vote ?? string.Empty).Trim().ToLowerInvariant();
        return v switch
        {
            "bullish" => "bullish",
            "bearish" => "bearish",
            _ => throw new ArgumentException($"Unsupported sentiment vote '{vote}'", nameof(vote)),
        };
    }

    public static class Votes
    {
        public const string Bullish = "bullish";
        public const string Bearish = "bearish";
        public const string None = "none";
    }
}
