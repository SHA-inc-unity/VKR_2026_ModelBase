using System.Collections.Concurrent;
using System.Text.Json;
using Confluent.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Kafka;

/// <summary>
/// Async request/reply client over Kafka.
/// Publishes a JSON envelope { correlation_id, reply_to, payload } to a command
/// topic and awaits a matching envelope on a private reply-inbox topic.
/// Runs as a hosted service: the consume-loop lifecycle is tied to the app.
/// </summary>
public sealed class KafkaRequestClient : IHostedService, IDisposable
{
    private readonly IProducer<string, string> _producer;
    private readonly IConsumer<string, string> _consumer;
    private readonly ILogger<KafkaRequestClient> _log;
    private readonly string _replyInbox;

    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();

    private CancellationTokenSource? _loopCts;
    private Task? _loopTask;

    public KafkaRequestClient(IOptions<KafkaSettings> opts, ILogger<KafkaRequestClient> log)
    {
        _log = log;
        var instanceId = Guid.NewGuid().ToString("N")[..8];
        _replyInbox = $"reply.gateway.{instanceId}";

        var bootstrap = opts.Value.BootstrapServers;

        _producer = new ProducerBuilder<string, string>(new ProducerConfig
        {
            BootstrapServers      = bootstrap,
            EnableDeliveryReports = true,
            MessageSendMaxRetries = 3,
            RetryBackoffMs        = 200,
        }).Build();

        _consumer = new ConsumerBuilder<string, string>(new ConsumerConfig
        {
            BootstrapServers               = bootstrap,
            GroupId                        = $"gateway-reply-{instanceId}",
            AutoOffsetReset                = AutoOffsetReset.Latest,
            EnableAutoCommit               = true,
            AllowAutoCreateTopics          = true,
            TopicMetadataRefreshIntervalMs = 5000,
        })
        .SetErrorHandler((_, err) =>
        {
            if (err.IsFatal)
                _log.LogError("Kafka fatal: {Code} {Reason}", err.Code, err.Reason);
            else
                _log.LogDebug("Kafka non-fatal: {Code} {Reason}", err.Code, err.Reason);
        })
        .Build();
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        _loopCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _consumer.Subscribe(_replyInbox);
        _loopTask = Task.Run(() => ConsumeLoopAsync(_loopCts.Token), _loopCts.Token);
        _log.LogInformation("KafkaRequestClient started, reply inbox: {Inbox}", _replyInbox);
        return Task.CompletedTask;
    }

    public async Task StopAsync(CancellationToken cancellationToken)
    {
        if (_loopCts is not null) await _loopCts.CancelAsync();
        if (_loopTask is not null)
        {
            try { await _loopTask.WaitAsync(cancellationToken); }
            catch (OperationCanceledException) { /* expected */ }
        }
        try { _consumer.Close(); } catch { /* ignore */ }
    }

    /// <summary>
    /// Send a request envelope to <paramref name="topic"/> and await the reply.
    /// Throws <see cref="TimeoutException"/> if no reply arrives within <paramref name="timeout"/>.
    /// </summary>
    public async Task<JsonElement> RequestAsync(
        string topic, object payload, TimeSpan timeout, CancellationToken ct = default)
    {
        var correlationId = Guid.NewGuid().ToString("N");
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[correlationId] = tcs;

        try
        {
            var envelope = new
            {
                correlation_id = correlationId,
                reply_to       = _replyInbox,
                payload,
            };
            var json = JsonSerializer.Serialize(envelope);

            await _producer.ProduceAsync(topic,
                new Message<string, string> { Key = correlationId, Value = json }, ct);

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(timeout);

            await using var _ = timeoutCts.Token.Register(() =>
                tcs.TrySetException(new TimeoutException($"Kafka request timed out on {topic}")));

            return await tcs.Task.ConfigureAwait(false);
        }
        finally
        {
            _pending.TryRemove(correlationId, out _);
        }
    }

    private async Task ConsumeLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            ConsumeResult<string, string>? result = null;
            try
            {
                result = _consumer.Consume(TimeSpan.FromMilliseconds(200));
            }
            catch (OperationCanceledException) { break; }
            catch (ConsumeException cex)
            {
                _log.LogDebug("Consume error: {Code} {Reason}", cex.Error.Code, cex.Error.Reason);
                await Task.Delay(500, ct);
                continue;
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Consume error");
                await Task.Delay(500, ct);
                continue;
            }

            if (result is null) continue;

            try
            {
                using var doc = JsonDocument.Parse(result.Message.Value);
                var root = doc.RootElement;
                var cid  = root.TryGetProperty("correlation_id", out var cidEl)
                    ? cidEl.GetString() ?? ""
                    : "";
                if (string.IsNullOrEmpty(cid)) continue;

                if (_pending.TryRemove(cid, out var tcs))
                {
                    var payload = root.TryGetProperty("payload", out var p)
                        ? p.Clone()
                        : default;
                    tcs.TrySetResult(payload);
                }
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Failed to parse reply envelope");
            }
        }
    }

    public void Dispose()
    {
        _producer.Dispose();
        _consumer.Dispose();
        _loopCts?.Dispose();
    }
}
