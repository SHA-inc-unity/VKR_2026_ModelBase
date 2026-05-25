using AccountService.Application.Interfaces.Repositories;
using AccountService.Domain.Entities;
using AccountService.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace AccountService.Infrastructure.Repositories;

public sealed class ExchangeApiKeyRepository : IExchangeApiKeyRepository
{
    private readonly AccountDbContext _db;

    public ExchangeApiKeyRepository(AccountDbContext db) => _db = db;

    public Task<List<ExchangeApiKey>> ListAsync(Guid userId, CancellationToken ct = default) =>
        _db.ExchangeApiKeys
            .Where(k => k.UserId == userId && k.Status != "revoked")
            .OrderByDescending(k => k.CreatedAt)
            .ToListAsync(ct);

    public Task<ExchangeApiKey?> GetActiveForExchangeAsync(Guid userId, string exchange, CancellationToken ct = default) =>
        _db.ExchangeApiKeys
            .Where(k => k.UserId == userId && k.Exchange == exchange && k.Status == "active")
            .OrderByDescending(k => k.CreatedAt)
            .FirstOrDefaultAsync(ct);

    public Task<ExchangeApiKey?> GetByIdAsync(Guid userId, Guid id, CancellationToken ct = default) =>
        _db.ExchangeApiKeys
            .FirstOrDefaultAsync(k => k.Id == id && k.UserId == userId, ct);

    public async Task AddAsync(ExchangeApiKey key, CancellationToken ct = default) =>
        await _db.ExchangeApiKeys.AddAsync(key, ct);

    public Task SaveChangesAsync(CancellationToken ct = default) =>
        _db.SaveChangesAsync(ct);
}
