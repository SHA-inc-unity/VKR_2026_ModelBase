using NotificationService.Application.DTOs;
using NotificationService.Application.Interfaces;
using NotificationService.Domain.Entities;

namespace NotificationService.Application.Services;

public interface INotificationsAppService
{
    Task<NotificationListResponse> ListAsync(Guid userId, bool unreadOnly, int page, int pageSize, CancellationToken ct);
    Task<int> GetUnreadCountAsync(Guid userId, CancellationToken ct);
    Task MarkReadAsync(Guid id, Guid userId, CancellationToken ct);
    Task MarkAllReadAsync(Guid userId, CancellationToken ct);

    Task<NotificationSettingsResponse> GetSettingsAsync(Guid userId, CancellationToken ct);
    Task<NotificationSettingsResponse> UpdateSettingsAsync(Guid userId, UpdateNotificationSettingsRequest req, CancellationToken ct);

    Task<bool> PushAsync(Notification n, CancellationToken ct);
}

public sealed class NotificationsAppService : INotificationsAppService
{
    private readonly INotificationRepository _repo;
    private readonly INotificationSettingsRepository _settings;
    private readonly ISseDispatcher _sse;
    private readonly IWebPushSender _push;

    public NotificationsAppService(
        INotificationRepository repo,
        INotificationSettingsRepository settings,
        ISseDispatcher sse,
        IWebPushSender push)
    {
        _repo = repo;
        _settings = settings;
        _sse = sse;
        _push = push;
    }

    public async Task<NotificationListResponse> ListAsync(Guid userId, bool unreadOnly, int page, int pageSize, CancellationToken ct)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 50;
        if (pageSize > 200) pageSize = 200;
        var slice = await _repo.ListAsync(userId, unreadOnly, page, pageSize, ct);
        return new NotificationListResponse
        {
            Items = slice.Items.Select(Map).ToList(),
            Total = slice.Total,
            Unread = slice.Unread,
            Page = page,
            PageSize = pageSize,
        };
    }

    public Task<int> GetUnreadCountAsync(Guid userId, CancellationToken ct) =>
        _repo.GetUnreadCountAsync(userId, ct);

    public async Task MarkReadAsync(Guid id, Guid userId, CancellationToken ct)
    {
        var n = await _repo.GetAsync(id, userId, ct);
        if (n is null) return;
        await _repo.MarkReadAsync(id, userId, ct);
    }

    public Task MarkAllReadAsync(Guid userId, CancellationToken ct) =>
        _repo.MarkAllReadAsync(userId, ct);

    public async Task<NotificationSettingsResponse> GetSettingsAsync(Guid userId, CancellationToken ct)
    {
        var s = await _settings.GetOrCreateAsync(userId, ct);
        return MapSettings(s);
    }

    public async Task<NotificationSettingsResponse> UpdateSettingsAsync(Guid userId, UpdateNotificationSettingsRequest req, CancellationToken ct)
    {
        var s = await _settings.GetOrCreateAsync(userId, ct);
        s.Update(req.EnableReply, req.EnableNews, req.EnablePrice, req.PriceThresholdPct);
        await _settings.SaveAsync(s, ct);
        return MapSettings(s);
    }

    public async Task<bool> PushAsync(Notification n, CancellationToken ct)
    {
        // Respect per-user opt-out.
        var s = await _settings.GetOrCreateAsync(n.UserId, ct);
        var allowed = n.Kind switch
        {
            "comment.reply" => s.EnableReply,
            "news.favorite" => s.EnableNews,
            "price.favorite" => s.EnablePrice,
            _ => true,
        };
        if (!allowed) return false;

        // Dedup.
        if (!string.IsNullOrEmpty(n.DedupKey))
        {
            if (await _repo.ExistsDedupAsync(n.UserId, n.Kind, n.DedupKey!, ct))
                return false;
        }

        await _repo.AddAsync(n, ct);
        await _sse.PushAsync(n.UserId, n);

        // Web Push mirrors the SSE path so the notification still arrives when the
        // tab/app is closed. It is best-effort and respects the per-kind opt-out
        // above (we already returned early if the user opted out). Never break inbox/SSE.
        try { await _push.SendAsync(n.UserId, n, ct); }
        catch { /* push is best-effort, never break inbox/SSE */ }

        return true;
    }

    private static NotificationResponse Map(Notification n) => new()
    {
        Id = n.Id,
        Kind = n.Kind,
        Title = n.Title,
        Body = n.Body,
        Deeplink = n.Deeplink,
        CreatedAt = n.CreatedAt,
        ReadAt = n.ReadAt,
    };

    private static NotificationSettingsResponse MapSettings(NotificationSettings s) => new()
    {
        EnableReply = s.EnableReply,
        EnableNews = s.EnableNews,
        EnablePrice = s.EnablePrice,
        PriceThresholdPct = s.PriceThresholdPct,
    };
}
