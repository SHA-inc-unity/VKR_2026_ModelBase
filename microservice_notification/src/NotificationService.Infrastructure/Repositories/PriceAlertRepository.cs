using Microsoft.EntityFrameworkCore;
using NotificationService.Application.Interfaces;
using NotificationService.Domain.Entities;
using NotificationService.Infrastructure.Data;

namespace NotificationService.Infrastructure.Repositories;

public sealed class PriceAlertRepository : IPriceAlertRepository
{
    private readonly NotificationDbContext _db;
    public PriceAlertRepository(NotificationDbContext db) => _db = db;

    public async Task<IReadOnlyList<PriceAlert>> ListByUserAsync(Guid userId, CancellationToken ct)
    {
        return await _db.PriceAlerts.AsNoTracking()
            .Where(x => x.UserId == userId)
            .OrderByDescending(x => x.CreatedAt)
            .ToListAsync(ct);
    }

    public Task<PriceAlert?> GetAsync(Guid id, Guid userId, CancellationToken ct) =>
        _db.PriceAlerts.FirstOrDefaultAsync(x => x.Id == id && x.UserId == userId, ct);

    public async Task AddAsync(PriceAlert alert, CancellationToken ct)
    {
        await _db.PriceAlerts.AddAsync(alert, ct);
        await _db.SaveChangesAsync(ct);
    }

    public async Task UpdateAsync(PriceAlert alert, CancellationToken ct)
    {
        _db.PriceAlerts.Update(alert);
        await _db.SaveChangesAsync(ct);
    }

    public async Task<bool> DeleteAsync(Guid id, Guid userId, CancellationToken ct)
    {
        var affected = await _db.PriceAlerts
            .Where(x => x.Id == id && x.UserId == userId)
            .ExecuteDeleteAsync(ct);
        return affected > 0;
    }

    public async Task<IReadOnlyList<PriceAlert>> ListEnabledAsync(CancellationToken ct)
    {
        return await _db.PriceAlerts
            .Where(x => x.IsEnabled)
            .ToListAsync(ct);
    }
}
