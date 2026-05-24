namespace SocialService.API.Kafka;

public sealed class KafkaSettings
{
    public const string SectionName = "Kafka";

    public string BootstrapServers { get; set; } = "redpanda:29092";
    public string SocialEventsTopic { get; set; } = "events.social.v1";
}
