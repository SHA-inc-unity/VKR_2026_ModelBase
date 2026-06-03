using NotificationService.Domain.Entities;

namespace NotificationService.Application.Interfaces;

public sealed class NotificationListPage
{
    public IReadOnlyList<Notification> Items { get; init; } = Array.Empty<Notification>();
    public int Total { get; init; }
    public int Unread { get; init; }
}

public interface INotificationRepository
{
    Task AddAsync(Notification n, CancellationToken ct);
    Task AddManyAsync(IEnumerable<Notification> items, CancellationToken ct);
    Task<NotificationListPage> ListAsync(Guid userId, bool unreadOnly, int page, int pageSize, CancellationToken ct);
    Task<int> GetUnreadCountAsync(Guid userId, CancellationToken ct);
    Task<Notification?> GetAsync(Guid id, Guid userId, CancellationToken ct);
    Task MarkReadAsync(Guid id, Guid userId, CancellationToken ct);
    Task MarkAllReadAsync(Guid userId, CancellationToken ct);

    /// <summary>Returns true if a row with the same (user, kind, dedup_key) already exists.</summary>
    Task<bool> ExistsDedupAsync(Guid userId, string kind, string dedupKey, CancellationToken ct);
}

public interface INotificationSettingsRepository
{
    Task<NotificationSettings> GetOrCreateAsync(Guid userId, CancellationToken ct);
    Task SaveAsync(NotificationSettings settings, CancellationToken ct);
}

public interface ISocialDirectoryService
{
    Task<Guid?> GetCommentAuthorAsync(Guid commentId, CancellationToken ct);
    Task<IReadOnlyList<Guid>> GetFavoriteUsersBySymbolAsync(string symbol, CancellationToken ct);

    /// All distinct favorited symbols across users — lets the price-drift
    /// watcher track exactly what people favorited instead of a static list.
    Task<IReadOnlyList<string>> GetAllFavoritedSymbolsAsync(CancellationToken ct);
}

public interface IMarketSnapshotService
{
    Task<IReadOnlyDictionary<string, decimal>> GetSnapshotAsync(IEnumerable<string> symbols, CancellationToken ct);
}

public interface ISseDispatcher
{
    Task PushAsync(Guid userId, Notification n);
}

public interface IPushSubscriptionRepository
{
    /// <summary>Insert a new subscription, or update the existing one (keyed by Endpoint):
    /// refresh keys + user agent, bump LastSeenAt, reset FailureCount.</summary>
    Task UpsertAsync(PushSubscription sub, CancellationToken ct);

    /// <summary>Remove a subscription owned by the user, identified by endpoint (idempotent).</summary>
    Task DeleteByEndpointAsync(Guid userId, string endpoint, CancellationToken ct);

    Task<IReadOnlyList<PushSubscription>> ListByUserAsync(Guid userId, CancellationToken ct);

    /// <summary>Hard-delete a single subscription by id (dead-subscription cleanup).</summary>
    Task DeleteAsync(Guid id, CancellationToken ct);

    /// <summary>Increment FailureCount for a subscription after a transient push failure.</summary>
    Task IncrementFailureAsync(Guid id, CancellationToken ct);
}

/// <summary>
/// Best-effort browser Web Push (VAPID) sender. Never throws: push delivery must
/// never break the inbox/SSE path. Disabled (soft, logged) when no private key is set.
/// </summary>
public interface IWebPushSender
{
    Task SendAsync(Guid userId, Notification n, CancellationToken ct);
}

/// <summary>
/// Persistence for user-defined price alerts. CRUD reads/writes are ownership-scoped
/// (user id), while <see cref="ListEnabledAsync"/> returns the cross-user batch the
/// evaluator polls.
/// </summary>
public interface IPriceAlertRepository
{
    Task<IReadOnlyList<PriceAlert>> ListByUserAsync(Guid userId, CancellationToken ct);
    Task<PriceAlert?> GetAsync(Guid id, Guid userId, CancellationToken ct);
    Task AddAsync(PriceAlert alert, CancellationToken ct);
    Task UpdateAsync(PriceAlert alert, CancellationToken ct);

    /// <summary>Delete an alert owned by the user; returns false if not found / not owned.</summary>
    Task<bool> DeleteAsync(Guid id, Guid userId, CancellationToken ct);

    /// <summary>All enabled alerts across every user — the evaluator's per-tick batch.</summary>
    Task<IReadOnlyList<PriceAlert>> ListEnabledAsync(CancellationToken ct);
}
