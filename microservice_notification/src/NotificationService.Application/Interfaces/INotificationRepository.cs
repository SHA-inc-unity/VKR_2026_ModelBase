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
}

public interface IMarketSnapshotService
{
    Task<IReadOnlyDictionary<string, decimal>> GetSnapshotAsync(IEnumerable<string> symbols, CancellationToken ct);
}

public interface ISseDispatcher
{
    Task PushAsync(Guid userId, Notification n);
}
