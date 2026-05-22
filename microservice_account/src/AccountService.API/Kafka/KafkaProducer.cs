using System.Text.Json;
using Confluent.Kafka;
using Microsoft.Extensions.Options;

namespace AccountService.API.Kafka;

public sealed class KafkaProducer : IDisposable
{
    private readonly IProducer<string, string> _producer;
    private readonly ILogger<KafkaProducer> _log;

    public KafkaProducer(IOptions<KafkaSettings> opts, ILogger<KafkaProducer> log)
    {
        _log = log;
        var cfg = new ProducerConfig
        {
            BootstrapServers      = opts.Value.BootstrapServers,
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

    public void Dispose() => _producer.Dispose();
}
