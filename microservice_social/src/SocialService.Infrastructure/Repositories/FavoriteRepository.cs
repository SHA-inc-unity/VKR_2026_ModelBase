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

    public async Task<IReadOnlyList<Guid>> GetUsersBySymbolAsync(string symbol, CancellationToken ct)
    {
        // Symbols are stored in the client's canonical form (e.g. "BTCUSDT"),
        // but callers may ask by base symbol ("BTC") — notably news tags. Match
        // on a small candidate set so BTC <-> BTCUSDT resolve to the same users.
        var candidates = SymbolCandidates(symbol);
        return await _db.Favorites.AsNoTracking()
            .Where(f => candidates.Contains(f.Symbol))
            .Select(f => f.UserId)
            .Distinct()
            .ToListAsync(ct);
    }

    public async Task<IReadOnlyList<string>> GetAllDistinctSymbolsAsync(CancellationToken ct) =>
        await _db.Favorites.AsNoTracking()
            .Select(f => f.Symbol)
            .Distinct()
            .ToListAsync(ct);

    // Common stablecoin quote suffixes used to derive a base symbol.
    private static readonly string[] QuoteSuffixes = ["USDT", "USDC", "BUSD", "USDP", "USD"];

    /// <summary>
    /// Builds the set of stored-symbol variants that should match a query.
    /// "BTC" -> {BTC, BTCUSDT, BTCUSDC, ...}; "BTCUSDT" -> {BTCUSDT, BTC, ...}.
    /// </summary>
    private static HashSet<string> SymbolCandidates(string symbol)
    {
        var s = symbol.Trim().ToUpperInvariant();
        var baseSymbol = s;
        foreach (var suffix in QuoteSuffixes)
        {
            if (s.Length > suffix.Length && s.EndsWith(suffix, StringComparison.Ordinal))
            {
                baseSymbol = s[..^suffix.Length];
                break;
            }
        }

        var set = new HashSet<string>(StringComparer.Ordinal) { s, baseSymbol };
        foreach (var suffix in QuoteSuffixes)
        {
            set.Add(baseSymbol + suffix);
        }
        return set;
    }
}
