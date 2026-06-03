using SocialService.Application.DTOs.Responses;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Application.Interfaces.Services;
using SocialService.Domain.Entities;

namespace SocialService.Application.Services;

public sealed class AssetSentimentAppService : IAssetSentimentAppService
{
    private readonly IAssetSentimentRepository _repo;

    public AssetSentimentAppService(IAssetSentimentRepository repo) => _repo = repo;

    public async Task<SentimentResponse> GetAsync(string targetType, string targetId, Guid? viewerUserId, CancellationToken ct)
    {
        var tt = Comment.NormalizeTargetType(targetType);
        var tid = (targetId ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(tid))
            throw new ArgumentException("targetId is required", nameof(targetId));

        var counts = await _repo.CountAsync(tt, tid, ct);
        var myVote = viewerUserId.HasValue
            ? await _repo.GetVoteAsync(viewerUserId.Value, tt, tid, ct)
            : null;

        return new SentimentResponse
        {
            Bullish = counts.Bullish,
            Bearish = counts.Bearish,
            Total = counts.Total,
            MyVote = myVote ?? AssetSentiment.Votes.None,
        };
    }

    public async Task<SentimentResponse> VoteAsync(Guid userId, string targetType, string targetId, string vote, CancellationToken ct)
    {
        var tt = Comment.NormalizeTargetType(targetType);
        var tid = (targetId ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(tid))
            throw new ArgumentException("targetId is required", nameof(targetId));

        var normalized = (vote ?? string.Empty).Trim().ToLowerInvariant();
        switch (normalized)
        {
            case AssetSentiment.Votes.None:
                await _repo.DeleteAsync(userId, tt, tid, ct);
                break;
            case AssetSentiment.Votes.Bullish:
            case AssetSentiment.Votes.Bearish:
                await _repo.UpsertAsync(userId, tt, tid, normalized, ct);
                break;
            default:
                throw new ArgumentException($"Unsupported sentiment vote '{vote}'", nameof(vote));
        }

        // Fresh aggregate projected for the voter (viewer = the actor).
        return await GetAsync(tt, tid, userId, ct);
    }
}
