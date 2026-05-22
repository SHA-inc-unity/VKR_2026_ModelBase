using AccountService.Application.Interfaces.Repositories;
using AccountService.Domain.Entities;
using AccountService.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace AccountService.Infrastructure.Repositories;

public sealed class RefreshTokenRepository : IRefreshTokenRepository
{
    private readonly AccountDbContext _db;

    public RefreshTokenRepository(AccountDbContext db) => _db = db;

    public Task<RefreshToken?> GetByHashAsync(string tokenHash, CancellationToken ct = default) =>
        _db.RefreshTokens
            .Include(t => t.User)
            .FirstOrDefaultAsync(t => t.TokenHash == tokenHash, ct);

    public async Task AddAsync(RefreshToken token, CancellationToken ct = default) =>
        await _db.RefreshTokens.AddAsync(token, ct);

    public async Task RevokeAsync(Guid tokenId, CancellationToken ct = default)
    {
        var token = await _db.RefreshTokens.FindAsync([tokenId], ct);
        token?.Revoke();
    }

    /// <summary>
    /// Set-based revoke: a single UPDATE statement, no entities materialised.
    /// Replaces the previous "load all + loop + Revoke()" path which round-tripped
    /// every active row of the user across the wire and held them in EF's
    /// change-tracker until SaveChangesAsync.
    /// </summary>
    public Task RevokeAllUserTokensAsync(Guid userId, CancellationToken ct = default)
    {
        var nowUtc = DateTimeOffset.UtcNow;
        return _db.RefreshTokens
            .Where(t => t.UserId == userId && t.RevokedAt == null)
            .ExecuteUpdateAsync(s => s.SetProperty(t => t.RevokedAt, nowUtc), ct);
    }

    public Task SaveChangesAsync(CancellationToken ct = default) =>
        _db.SaveChangesAsync(ct);
}
