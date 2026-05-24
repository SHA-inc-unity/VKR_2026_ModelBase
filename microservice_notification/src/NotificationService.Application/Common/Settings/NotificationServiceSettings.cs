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
