using Microsoft.EntityFrameworkCore;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Domain.Entities;
using SocialService.Infrastructure.Data;

namespace SocialService.Infrastructure.Repositories;

public sealed class FavoriteRepository : IFavoriteRepository
{
    private readonly SocialDbContext _db;
    public FavoriteRepository(SocialDbContext db) => _db = db;

    public async Task<IReadOnlyList<string>> GetSymbolsAsync(Guid userId, CancellationToken ct) =>
        await _db.Favorites.AsNoTracking()
            .Where(f => f.UserId == userId)
            .OrderByDescending(f => f.CreatedAt)
            .Select(f => f.Symbol)
            .ToListAsync(ct);

    public Task<bool> ExistsAsync(Guid userId, string symbol, CancellationToken ct) =>
        _db.Favorites.AsNoTracking()
            .AnyAsync(f => f.UserId == userId && f.Symbol == symbol, ct);

    public async Task AddAsync(Favorite favorite, CancellationToken ct)
    {
        await _db.Favorites.AddAsync(favorite, ct);
        await _db.SaveChangesAsync(ct);
    }

    public async Task<bool> RemoveAsync(Guid userId, string symbol, CancellationToken ct)
    {
        var entity = await _db.Favorites.FirstOrDefaultAsync(
            f => f.UserId == userId && f.Symbol == symbol, ct);
        if (entity is null) return false;
        _db.Favorites.Remove(entity);
        await _db.SaveChangesAsync(ct);
        return true;
    }

    public async Task<IReadOnlyList<Guid>> GetUsersBySymbolAsync(string symbol, CancellationToken ct) =>
        await _db.Favorites.AsNoTracking()
            .Where(f => f.Symbol == symbol)
            .Select(f => f.UserId)
            .Distinct()
            .ToListAsync(ct);
}
