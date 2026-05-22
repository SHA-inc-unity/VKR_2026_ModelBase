using System.Text.Json;
using Confluent.Kafka;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Kafka;

public sealed class KafkaProducer : IDisposable
{
    private readonly IProducer<string, string> _producer;
    private readonly ILogger<KafkaProducer> _log;

    public KafkaProducer(IOptions<DataServiceSettings> opts, ILogger<KafkaProducer> log)
    {
        _log = log;
        var cfg = new ProducerConfig
        {
            BootstrapServers      = opts.Value.Kafka.BootstrapServers,
            EnableDeliveryReports = true,
            MessageSendMaxRetries = 3,
            RetryBackoffMs        = 200,
        };
        _producer = new ProducerBuilder<string, string>(cfg).Build();
    }

    /// <summary>
    /// Publish a reply envelope { correlation_id, payload } to the given reply topic.
    /// </summary>
    public async Task PublishReplyAsync(
        string replyTopic, string correlationId, object payload,
        CancellationToken ct = default)
    {
        var envelope = new { correlation_id = correlationId, payload };
        var json = JsonSerializer.Serialize(envelope);
        try
        {
            await _producer.ProduceAsync(replyTopic,
                new Message<string, string> { Key = correlationId, Value = json }, ct);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to publish reply to {Topic}", replyTopic);
        }
    }

    /// <summary>
    /// Fire-and-forget publication of an event to the given topic. Unlike
    /// <see cref="PublishReplyAsync"/>, there is no correlation_id/reply_to
    /// envelope — callers are responsible for placing any correlation fields
    /// directly inside <paramref name="payload"/>. Errors are logged but not
    /// rethrown: events are non-critical progress signals and must never
    /// break the caller's main flow.
    /// </summary>
    public async Task PublishEventAsync(
        string topic, object payload, CancellationToken ct = default)
    {
        try
        {
            var json = JsonSerializer.Serialize(payload);
            await _producer.ProduceAsync(topic,
                new Message<string, string> { Value = json }, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to publish event to {Topic}", topic);
        }
    }

    public void Dispose() => _producer.Dispose();
}
