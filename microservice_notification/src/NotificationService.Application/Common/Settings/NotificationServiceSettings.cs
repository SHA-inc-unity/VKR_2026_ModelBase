namespace NotificationService.Application.Common.Settings;

public sealed class JwtSettings
{
    public const string SectionName = "Jwt";
    public string SecretKey { get; set; } = string.Empty;
    public string Issuer { get; set; } = "account-service";
    public string Audience { get; set; } = "exchange-app";
}

public sealed class NotificationKafkaSettings
{
    public const string SectionName = "Kafka";
    public string BootstrapServers { get; set; } = "redpanda:29092";
    public string SocialEventsTopic { get; set; } = "events.social.v1";
    public string NewsEventsTopic { get; set; } = "events.news.v1";
    public string GroupId { get; set; } = "notification-service";
}

public sealed class SocialServiceSettings
{
    public const string SectionName = "SocialService";
    public string BaseUrl { get; set; } = "http://social_service_api:5000";
    public string InternalApiKey { get; set; } = string.Empty;
}

public sealed class GatewaySettings
{
    public const string SectionName = "Gateway";
    public string BaseUrl { get; set; } = "http://exchange-gateway:5000";
}

public sealed class PriceWatcherSettings
{
    public const string SectionName = "PriceWatcher";
    public int PollIntervalSeconds { get; set; } = 300;
    public bool Enabled { get; set; } = true;
}

/// <summary>
/// Cadence / kill-switch for <c>PriceAlertEvaluatorService</c>, the watcher that
/// evaluates user-defined price alerts against live prices.
/// </summary>
public sealed class AlertWatcherSettings
{
    public const string SectionName = "AlertWatcher";
    public int PollIntervalSeconds { get; set; } = 60;
    public bool Enabled { get; set; } = true;
}

/// <summary>
/// Self-hosted Web Push (VAPID) configuration. The public key is safe to commit
/// and is served to browsers; the private key is a secret injected via env only.
/// </summary>
public sealed class PushSettings
{
    public const string SectionName = "Push";

    public string VapidPublicKey { get; set; } = string.Empty;
    public string VapidPrivateKey { get; set; } = string.Empty;
    public string VapidSubject { get; set; } = "mailto:admin@sha-trade.tech";

    /// <summary>Push delivery is only active once both VAPID keys are present.</summary>
    public bool Enabled =>
        !string.IsNullOrWhiteSpace(VapidPublicKey) && !string.IsNullOrWhiteSpace(VapidPrivateKey);
}
