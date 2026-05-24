using System.Text.Json;
using Confluent.Kafka;
using Microsoft.Extensions.Options;
using NewsService.Application.Common.Settings;
using NewsService.Application.Interfaces;
using NewsService.Domain.Entities;

namespace NewsService.API.Kafka;

public sealed class KafkaNewsEventBus : INewsEventBus, IDisposable
{
    private readonly IProducer<string, string> _producer;
    private readonly NewsKafkaSettings _settings;
    private readonly ILogger<KafkaNewsEventBus> _log;
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public KafkaNewsEventBus(IOptions<NewsKafkaSettings> opts, ILogger<KafkaNewsEventBus> log)
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

    public async Task PublishCreatedAsync(NewsArticle article, CancellationToken ct)
    {
        var envelope = new
        {
            type = "news.created",
            occurredAt = DateTime.UtcNow,
            payload = new
            {
                newsId = article.Id,
                title = article.Title,
                tags = article.Tags,
                source = article.Source,
                sourceUrl = article.SourceUrl,
                publishedAt = article.PublishedAt,
            },
        };
        var json = JsonSerializer.Serialize(envelope, JsonOpts);
        try
        {
            await _producer.ProduceAsync(
                _settings.NewsEventsTopic,
                new Message<string, string>
                {
                    Key = article.Id.ToString(),
                    Value = json,
                },
                ct);
            _log.LogDebug("Published news.created for {NewsId}", article.Id);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to publish news.created for {NewsId}", article.Id);
        }
    }

    public void Dispose() => _producer.Dispose();
}
