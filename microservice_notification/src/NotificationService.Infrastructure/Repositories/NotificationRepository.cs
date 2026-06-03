using Microsoft.EntityFrameworkCore;
using NotificationService.Application.Interfaces;
using NotificationService.Domain.Entities;
using NotificationService.Infrastructure.Data;

namespace NotificationService.Infrastructure.Repositories;

public sealed class NotificationRepository : INotificationRepository
{
    private readonly NotificationDbContext _db;
    public NotificationRepository(NotificationDbContext db) => _db = db;

    public async Task AddAsync(Notification n, CancellationToken ct)
    {
        await _db.Notifications.AddAsync(n, ct);
        await _db.SaveChangesAsync(ct);
    }

    public async Task AddManyAsync(IEnumerable<Notification> items, CancellationToken ct)
    {
        await _db.Notifications.AddRangeAsync(items, ct);
        await _db.SaveChangesAsync(ct);
    }

    public async Task<NotificationListPage> ListAsync(Guid userId, bool unreadOnly, int page, int pageSize, CancellationToken ct)
    {
        var baseQ = _db.Notifications.AsNoTracking().Where(x => x.UserId == userId);
        var unread = await baseQ.CountAsync(x => x.ReadAt == null, ct);

        var q = unreadOnly ? baseQ.Where(x => x.ReadAt == null) : baseQ;
        var total = await q.CountAsync(ct);
        var items = await q
            .OrderByDescending(x => x.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(ct);

        return new NotificationListPage { Items = items, Total = total, Unread = unread };
    }

    public Task<int> GetUnreadCountAsync(Guid userId, CancellationToken ct) =>
        _db.Notifications.AsNoTracking().CountAsync(x => x.UserId == userId && x.ReadAt == null, ct);

    public Task<Notification?> GetAsync(Guid id, Guid userId, CancellationToken ct) =>
        _db.Notifications.FirstOrDefaultAsync(x => x.Id == id && x.UserId == userId, ct);

    public async Task MarkReadAsync(Guid id, Guid userId, CancellationToken ct)
    {
        var n = await _db.Notifications.FirstOrDefaultAsync(x => x.Id == id && x.UserId == userId, ct);
        if (n is null || n.ReadAt is not null) return;
        n.MarkRead();
        await _db.SaveChangesAsync(ct);
    }

    public async Task MarkAllReadAsync(Guid userId, CancellationToken ct)
    {
        var now = DateTime.UtcNow;
        await _db.Notifications
            .Where(x => x.UserId == userId && x.ReadAt == null)
            .ExecuteUpdateAsync(set => set.SetProperty(x => x.ReadAt, now), ct);
    }

    public Task<bool> ExistsDedupAsync(Guid userId, string kind, string dedupKey, CancellationToken ct) =>
        _db.Notifications.AsNoTracking()
            .AnyAsync(x => x.UserId == userId && x.Kind == kind && x.DedupKey == dedupKey, ct);
}

public sealed class NotificationSettingsRepository : INotificationSettingsRepository
{
    private readonly NotificationDbContext _db;
    public NotificationSettingsRepository(NotificationDbContext db) => _db = db;

    public async Task<NotificationSettings> GetOrCreateAsync(Guid userId, CancellationToken ct)
    {
        var existing = await _db.NotificationSettings.FirstOrDefaultAsync(x => x.UserId == userId, ct);
        if (existing is not null) return existing;

        var fresh = NotificationSettings.Default(userId);
        await _db.NotificationSettings.AddAsync(fresh, ct);
        try
        {
            await _db.SaveChangesAsync(ct);
        }
        catch (DbUpdateException)
        {
            // Concurrent insert: re-read.
            _db.Entry(fresh).State = EntityState.Detached;
            existing = await _db.NotificationSettings.FirstOrDefaultAsync(x => x.UserId == userId, ct);
            if (existing is not null) return existing;
            throw;
        }
        return fresh;
    }

    public async Task SaveAsync(NotificationSettings settings, CancellationToken ct)
    {
        _db.NotificationSettings.Update(settings);
        await _db.SaveChangesAsync(ct);
    }
}

public sealed class PushSubscriptionRepository : IPushSubscriptionRepository
{
    private readonly NotificationDbContext _db;
    public PushSubscriptionRepository(NotificationDbContext db) => _db = db;

    public async Task UpsertAsync(PushSubscription sub, CancellationToken ct)
    {
        var existing = await _db.PushSubscriptions.FirstOrDefaultAsync(x => x.Endpoint == sub.Endpoint, ct);
        if (existing is not null)
        {
            // Same endpoint can be re-subscribed under a (possibly) new user/keys.
            existing.Refresh(sub.P256dh, sub.Auth, sub.UserAgent);
            await _db.SaveChangesAsync(ct);
            return;
        }

        await _db.PushSubscriptions.AddAsync(sub, ct);
        try
        {
            await _db.SaveChangesAsync(ct);
        }
        catch (DbUpdateException)
        {
            // Concurrent double-submit raced us on the unique endpoint index:
            // detach our insert and refresh the row that won instead.
            _db.Entry(sub).State = EntityState.Detached;
            existing = await _db.PushSubscriptions.FirstOrDefaultAsync(x => x.Endpoint == sub.Endpoint, ct);
            if (existing is null) throw;

            existing.Refresh(sub.P256dh, sub.Auth, sub.UserAgent);
            await _db.SaveChangesAsync(ct);
        }
    }

    public async Task DeleteByEndpointAsync(Guid userId, string endpoint, CancellationToken ct)
    {
        await _db.PushSubscriptions
            .Where(x => x.UserId == userId && x.Endpoint == endpoint)
            .ExecuteDeleteAsync(ct);
    }

    public async Task<IReadOnlyList<PushSubscription>> ListByUserAsync(Guid userId, CancellationToken ct)
    {
        return await _db.PushSubscriptions.AsNoTracking()
            .Where(x => x.UserId == userId)
            .ToListAsync(ct);
    }

    public async Task DeleteAsync(Guid id, CancellationToken ct)
    {
        await _db.PushSubscriptions
            .Where(x => x.Id == id)
            .ExecuteDeleteAsync(ct);
    }

    public async Task IncrementFailureAsync(Guid id, CancellationToken ct)
    {
        await _db.PushSubscriptions
            .Where(x => x.Id == id)
            .ExecuteUpdateAsync(set => set.SetProperty(x => x.FailureCount, x => x.FailureCount + 1), ct);
    }
}
