using System.Net;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using NotificationService.Application.Common.Settings;
using NotificationService.Application.Interfaces;
using WebPush;
using DomainNotification = NotificationService.Domain.Entities.Notification;
using DomainPushSubscription = NotificationService.Domain.Entities.PushSubscription;
using LibPushSubscription = WebPush.PushSubscription;

namespace NotificationService.Infrastructure.PushNotifications;

/// <summary>
/// Self-hosted Web Push (VAPID) delivery via the WebPush NuGet package. Mirrors the
/// SSE path so a notification reaches a browser even when the tab/app is closed.
///
/// Best-effort by contract: never throws out of <see cref="SendAsync"/>. When the
/// VAPID private key is empty, push is disabled — we log a single warning and return.
/// Dead subscriptions (HTTP 404/410 Gone) are deleted; other failures bump the
/// failure counter (best-effort) and continue.
/// </summary>
public sealed class WebPushSender : IWebPushSender
{
    private readonly IPushSubscriptionRepository _subs;
    private readonly PushSettings _settings;
    private readonly ILogger<WebPushSender> _logger;
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);

    // Log the "push disabled" warning only once per process to avoid log spam.
    private int _disabledWarned;

    public WebPushSender(
        IPushSubscriptionRepository subs,
        IOptions<PushSettings> settings,
        ILogger<WebPushSender> logger)
    {
        _subs = subs;
        _settings = settings.Value;
        _logger = logger;
    }

    public async Task SendAsync(Guid userId, DomainNotification n, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(_settings.VapidPrivateKey))
        {
            if (Interlocked.Exchange(ref _disabledWarned, 1) == 0)
                _logger.LogWarning("Web Push disabled: no VAPID private key configured (Push:VapidPrivateKey). Skipping push delivery.");
            return;
        }

        IReadOnlyList<DomainPushSubscription> subscriptions;
        try
        {
            subscriptions = await _subs.ListByUserAsync(userId, ct);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Web Push: failed to load subscriptions for user {UserId}", userId);
            return;
        }

        if (subscriptions.Count == 0) return;

        var payload = JsonSerializer.Serialize(
            new
            {
                title = n.Title,
                body = n.Body,
                deeplink = n.Deeplink,
                kind = n.Kind,
                id = n.Id,
            },
            JsonOpts);

        var vapid = new VapidDetails(_settings.VapidSubject, _settings.VapidPublicKey, _settings.VapidPrivateKey);
        var client = new WebPushClient();

        foreach (var sub in subscriptions)
        {
            if (ct.IsCancellationRequested) break;
            try
            {
                var libSub = new LibPushSubscription(sub.Endpoint, sub.P256dh, sub.Auth);
                // WebPush 1.0.12's async overload does not accept a CancellationToken.
                await client.SendNotificationAsync(libSub, payload, vapid);
            }
            catch (WebPushException ex)
            {
                if (ex.StatusCode == HttpStatusCode.NotFound || ex.StatusCode == HttpStatusCode.Gone)
                {
                    // Subscription is dead — purge it so we stop trying.
                    _logger.LogInformation(
                        "Web Push: dropping dead subscription {SubscriptionId} for user {UserId} (HTTP {Status})",
                        sub.Id, userId, (int)ex.StatusCode);
                    try { await _subs.DeleteAsync(sub.Id, ct); }
                    catch (Exception delEx) { _logger.LogWarning(delEx, "Web Push: failed to delete dead subscription {SubscriptionId}", sub.Id); }
                }
                else
                {
                    _logger.LogWarning(
                        ex, "Web Push: transient failure for subscription {SubscriptionId} (HTTP {Status})",
                        sub.Id, (int)ex.StatusCode);
                    await BumpFailureAsync(sub, ct);
                }
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Web Push: unexpected failure for subscription {SubscriptionId}", sub.Id);
                await BumpFailureAsync(sub, ct);
            }
        }
    }

    private async Task BumpFailureAsync(DomainPushSubscription sub, CancellationToken ct)
    {
        try
        {
            await _subs.IncrementFailureAsync(sub.Id, ct);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Web Push: failed to record failure for subscription {SubscriptionId}", sub.Id);
        }
    }
}
