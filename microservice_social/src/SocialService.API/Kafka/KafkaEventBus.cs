using System.Text.Json;
using Confluent.Kafka;
using Microsoft.Extensions.Options;
using SocialService.Application.Interfaces.Services;

namespace SocialService.API.Kafka;

public sealed class KafkaEventBus : IEventBus, IDisposable
{
    private readonly IProducer<string, string> _producer;
    private readonly KafkaSettings _settings;
    private readonly ILogger<KafkaEventBus> _log;
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public KafkaEventBus(IOptions<KafkaSettings> opts, ILogger<KafkaEventBus> log)
    {
        _settings = opts.Value;
        _log = log;
        var cfg = new ProducerConfig
        {
            BootstrapServers = _settings.BootstrapServers,
            EnableDeliveryReports = true,
            MessageSendMaxRetries = 3,
            RetryBackoffMs = 200,
        };
        _producer = new ProducerBuilder<string, string>(cfg).Build();
    }

    public async Task PublishAsync(string eventType, object payload, CancellationToken ct)
    {
        var envelope = new
        {
            type = eventType,
            occurredAt = DateTime.UtcNow,
            payload,
        };
        var json = JsonSerializer.Serialize(envelope, JsonOpts);
        try
        {
            await _producer.ProduceAsync(
                _settings.SocialEventsTopic,
                new Message<string, string>
                {
                    Key = eventType,
                    Value = json,
                },
                ct);
            _log.LogDebug("Published {EventType} to {Topic}", eventType, _settings.SocialEventsTopic);
        }
        catch (Exception ex)
        {
            // Do not break the request if Kafka is down — notifications are best-effort.
            _log.LogWarning(ex, "Failed to publish social event {EventType}", eventType);
        }
    }

    public void Dispose() => _producer.Dispose();
}
