using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Threading;
using Confluent.Kafka;
using Confluent.Kafka.Admin;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Kafka;

/// <summary>
/// Async request/reply client over Kafka.
/// Publishes a JSON envelope { correlation_id, reply_to, payload } to a command
/// topic and awaits a matching envelope on a private reply-inbox topic.
/// Runs as a hosted service: the consume-loop lifecycle is tied to the app.
/// </summary>
public sealed class KafkaRequestClient : IKafkaRequestClient, IHostedService, IDisposable
{
    private static readonly TimeSpan ReplyInboxStartupBudget = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan ReplyInboxRetryDelay = TimeSpan.FromSeconds(1);

    private readonly IAdminClient _admin;
    private readonly IProducer<string, string> _producer;
    private readonly IConsumer<string, string> _consumer;
    private readonly ILogger<KafkaRequestClient> _log;
    private readonly string _replyInbox;

    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly TaskCompletionSource<bool> _replyInboxReady =
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private CancellationTokenSource? _loopCts;
    private Task? _loopTask;
    private int _isReplyInboxReady;

    public KafkaRequestClient(IOptions<KafkaSettings> opts, ILogger<KafkaRequestClient> log)
    {
        _log = log;
        var instanceId = Guid.NewGuid().ToString("N")[..8];
        _replyInbox = $"reply.gateway.{instanceId}";

        var bootstrap = opts.Value.BootstrapServers;

        _admin = new AdminClientBuilder(new AdminClientConfig
        {
            BootstrapServers = bootstrap,
        }).Build();

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
            AllowAutoCreateTopics          = false,
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
        _loopTask = Task.Run(() => BootstrapAndConsumeAsync(_loopCts.Token), CancellationToken.None);
        _log.LogInformation(
            "KafkaRequestClient starting background bootstrap, reply inbox: {Inbox}",
            _replyInbox);
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
        var startedAt = Stopwatch.GetTimestamp();
        await WaitForReplyInboxReadyAsync(startedAt, timeout, ct);

        var remainingTimeout = timeout - Stopwatch.GetElapsedTime(startedAt);
        if (remainingTimeout <= TimeSpan.Zero)
        {
            throw new TimeoutException($"Kafka request timed out waiting for reply inbox on {topic}");
        }

        var correlationId = Guid.NewGuid().ToString("N");
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[correlationId] = tcs;

        _log.LogInformation(
            "KafkaRequest start topic={Topic} correlationId={CorrelationId} replyInbox={ReplyInbox} timeoutMs={TimeoutMs} pendingCount={PendingCount}",
            topic,
            correlationId,
            _replyInbox,
            (int)remainingTimeout.TotalMilliseconds,
            _pending.Count);

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
            _log.LogInformation(
                "KafkaRequest produced topic={Topic} correlationId={CorrelationId} replyInbox={ReplyInbox} durationMs={DurationMs}",
                topic,
                correlationId,
                _replyInbox,
                (int)Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds);

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(remainingTimeout);

            await using var _ = timeoutCts.Token.Register(() =>
            {
                _log.LogWarning(
                    "KafkaRequest timeout topic={Topic} correlationId={CorrelationId} replyInbox={ReplyInbox} timeoutMs={TimeoutMs} pendingCount={PendingCount}",
                    topic,
                    correlationId,
                    _replyInbox,
                    (int)remainingTimeout.TotalMilliseconds,
                    _pending.Count);
                tcs.TrySetException(new TimeoutException($"Kafka request timed out on {topic}"));
            });

            var reply = await tcs.Task.ConfigureAwait(false);
            _log.LogInformation(
                "KafkaRequest success topic={Topic} correlationId={CorrelationId} replyInbox={ReplyInbox} durationMs={DurationMs}",
                topic,
                correlationId,
                _replyInbox,
                (int)Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds);
            return reply;
        }
        catch (Exception ex) when (ex is not TimeoutException)
        {
            _log.LogError(ex,
                "KafkaRequest failed topic={Topic} correlationId={CorrelationId} replyInbox={ReplyInbox} durationMs={DurationMs}",
                topic,
                correlationId,
                _replyInbox,
                (int)Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds);
            throw;
        }
        finally
        {
            _pending.TryRemove(correlationId, out _);
        }
    }

    private async Task BootstrapAndConsumeAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                if (Volatile.Read(ref _isReplyInboxReady) == 0)
                {
                    await EnsureReplyInboxTopicAsync(ct);
                    _consumer.Subscribe(_replyInbox);
                    Interlocked.Exchange(ref _isReplyInboxReady, 1);
                    _replyInboxReady.TrySetResult(true);
                    _log.LogInformation("KafkaRequestClient started, reply inbox: {Inbox}", _replyInbox);
                }

                await ConsumeLoopAsync(ct);
                return;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _log.LogError(ex,
                    "KafkaRequestClient bootstrap failed for reply inbox {Inbox}; retrying in {DelayMs} ms",
                    _replyInbox,
                    (int)ReplyInboxRetryDelay.TotalMilliseconds);
                await Task.Delay(ReplyInboxRetryDelay, ct);
            }
        }
    }

    private async Task WaitForReplyInboxReadyAsync(
        long startedAt,
        TimeSpan timeout,
        CancellationToken ct)
    {
        if (Volatile.Read(ref _isReplyInboxReady) == 1)
        {
            return;
        }

        var remainingTimeout = timeout - Stopwatch.GetElapsedTime(startedAt);
        if (remainingTimeout <= TimeSpan.Zero)
        {
            throw new TimeoutException("Kafka reply inbox is not ready");
        }

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeoutCts.CancelAfter(remainingTimeout);

        try
        {
            await _replyInboxReady.Task.WaitAsync(timeoutCts.Token);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new TimeoutException("Kafka reply inbox is not ready");
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
                else
                {
                    _log.LogDebug(
                        "KafkaRequest unmatched reply correlationId={CorrelationId} replyInbox={ReplyInbox}",
                        cid,
                        _replyInbox);
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
        _admin.Dispose();
        _producer.Dispose();
        _consumer.Dispose();
        _loopCts?.Dispose();
    }

    private async Task EnsureReplyInboxTopicAsync(CancellationToken ct)
    {
        var deadline = DateTime.UtcNow + ReplyInboxStartupBudget;

        _log.LogInformation(
            "KafkaRequestClient ensuring reply inbox topic {Inbox} startupBudgetMs={StartupBudgetMs}",
            _replyInbox,
            (int)ReplyInboxStartupBudget.TotalMilliseconds);

        while (true)
        {
            ct.ThrowIfCancellationRequested();

            try
            {
                await _admin.CreateTopicsAsync(
                    [new TopicSpecification
                    {
                        Name = _replyInbox,
                        NumPartitions = 1,
                        ReplicationFactor = 1,
                    }],
                    new CreateTopicsOptions
                    {
                        RequestTimeout = ReplyInboxStartupBudget,
                        OperationTimeout = ReplyInboxStartupBudget,
                    });

                _log.LogInformation("KafkaRequestClient created reply inbox topic {Inbox}", _replyInbox);
                await Task.Delay(TimeSpan.FromMilliseconds(500), ct);
                return;
            }
            catch (CreateTopicsException ex) when (ex.Results.All(r => r.Error.Code == ErrorCode.TopicAlreadyExists))
            {
                _log.LogDebug("KafkaRequestClient reply inbox topic already exists: {Inbox}", _replyInbox);
                await Task.Delay(TimeSpan.FromMilliseconds(500), ct);
                return;
            }
            catch (Exception ex) when (DateTime.UtcNow < deadline)
            {
                _log.LogWarning(ex,
                    "KafkaRequestClient failed to create reply inbox {Inbox}; retrying",
                    _replyInbox);
                await Task.Delay(ReplyInboxRetryDelay, ct);
            }
        }
    }
}
