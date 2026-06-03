using SocialService.Domain.Entities;

namespace SocialService.Application.Interfaces.Repositories;

/// <summary>
/// Aggregate vote counts for a single (targetType, targetId). Mirrors the
/// GROUP BY shape used by comment likes, but split by vote direction.
/// </summary>
public readonly record struct SentimentCounts(int Bullish, int Bearish)
{
    public int Total => Bullish + Bearish;
}

public interface IAssetSentimentRepository
{
    /// <summary>Counts bullish/bearish votes for the target via GROUP BY Vote.</summary>
    Task<SentimentCounts> CountAsync(string targetType, string targetId, CancellationToken ct);

    /// <summary>The viewer's current vote ("bullish"/"bearish"), or null if none.</summary>
    Task<string?> GetVoteAsync(Guid userId, string targetType, string targetId, CancellationToken ct);

    /// <summary>Inserts or moves the single (user, target) vote row.</summary>
    Task UpsertAsync(Guid userId, string targetType, string targetId, string vote, CancellationToken ct);

    /// <summary>Retracts the vote (DELETE). Returns true if a row was removed.</summary>
    Task<bool> DeleteAsync(Guid userId, string targetType, string targetId, CancellationToken ct);
}
