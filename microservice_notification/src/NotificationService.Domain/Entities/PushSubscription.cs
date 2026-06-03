namespace NotificationService.Domain.Entities;

/// <summary>
/// A browser Web Push (VAPID) subscription owned by a user. One row per
/// browser/device endpoint. The same user can hold many subscriptions.
/// </summary>
public sealed class PushSubscription
{
    public Guid Id { get; private set; }
    public Guid UserId { get; private set; }

    /// <summary>The push service endpoint URL — globally unique per browser/device.</summary>
    public string Endpoint { get; private set; } = string.Empty;

    /// <summary>The client public key (base64url) used to encrypt the payload.</summary>
    public string P256dh { get; private set; } = string.Empty;

    /// <summary>The client auth secret (base64url) used to encrypt the payload.</summary>
    public string Auth { get; private set; } = string.Empty;

    public string? UserAgent { get; private set; }
    public DateTime CreatedAt { get; private set; }
    public DateTime LastSeenAt { get; private set; }

    /// <summary>Consecutive delivery failures; used to age out flaky subscriptions.</summary>
    public int FailureCount { get; private set; }

    private PushSubscription() { }

    public static PushSubscription Create(
        Guid userId,
        string endpoint,
        string p256dh,
        string auth,
        string? userAgent)
    {
        var now = DateTime.UtcNow;
        return new PushSubscription
        {
            Id = Guid.NewGuid(),
            UserId = userId,
            Endpoint = endpoint,
            P256dh = p256dh,
            Auth = auth,
            UserAgent = userAgent,
            CreatedAt = now,
            LastSeenAt = now,
            FailureCount = 0,
        };
    }

    /// <summary>Refresh keys + metadata on a re-subscribe; resets the failure counter and last-seen.</summary>
    public void Refresh(string p256dh, string auth, string? userAgent)
    {
        P256dh = p256dh;
        Auth = auth;
        UserAgent = userAgent;
        LastSeenAt = DateTime.UtcNow;
        FailureCount = 0;
    }
}
