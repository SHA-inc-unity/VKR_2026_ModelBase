namespace SocialService.Application.Interfaces.Services;

/// <summary>
/// Publishes domain events to Kafka topic events.social.v1.
/// </summary>
public interface IEventBus
{
    Task PublishAsync(string eventType, object payload, CancellationToken ct);
}
