using Microsoft.EntityFrameworkCore;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Domain.Entities;
using SocialService.Infrastructure.Data;

namespace SocialService.Infrastructure.Repositories;

public sealed class AssetSentimentRepository : IAssetSentimentRepository
{
    private readonly SocialDbContext _db;
    public AssetSentimentRepository(SocialDbContext db) => _db = db;

    public async Task<SentimentCounts> CountAsync(string targetType, string targetId, CancellationToken ct)
    {
        var grouped = await _db.AssetSentiments.AsNoTracking()
            .Where(s => s.TargetType == targetType && s.TargetId == targetId)
            .GroupBy(s => s.Vote)
            .Select(g => new { Vote = g.Key, Count = g.Count() })
            .ToListAsync(ct);

        var bullish = 0;
        var bearish = 0;
        foreach (var row in grouped)
        {
            if (row.Vote == AssetSentiment.Votes.Bullish) bullish = row.Count;
            else if (row.Vote == AssetSentiment.Votes.Bearish) bearish = row.Count;
        }
        return new SentimentCounts(bullish, bearish);
    }

    public Task<string?> GetVoteAsync(Guid userId, string targetType, string targetId, CancellationToken ct) =>
        _db.AssetSentiments.AsNoTracking()
            .Where(s => s.UserId == userId && s.TargetType == targetType && s.TargetId == targetId)
            .Select(s => s.Vote)
            .FirstOrDefaultAsync(ct);

    public async Task UpsertAsync(Guid userId, string targetType, string targetId, string vote, CancellationToken ct)
    {
        var existing = await _db.AssetSentiments.FirstOrDefaultAsync(
            s => s.UserId == userId && s.TargetType == targetType && s.TargetId == targetId, ct);

        if (existing is null)
        {
            await _db.AssetSentiments.AddAsync(
                AssetSentiment.Create(userId, targetType, targetId, vote), ct);
        }
        else
        {
            existing.Change(vote);
            _db.AssetSentiments.Update(existing);
        }
        await _db.SaveChangesAsync(ct);
    }

    public async Task<bool> DeleteAsync(Guid userId, string targetType, string targetId, CancellationToken ct)
    {
        var entity = await _db.AssetSentiments.FirstOrDefaultAsync(
            s => s.UserId == userId && s.TargetType == targetType && s.TargetId == targetId, ct);
        if (entity is null) return false;
        _db.AssetSentiments.Remove(entity);
        await _db.SaveChangesAsync(ct);
        return true;
    }
}
