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
public sealed class KafkaRequestClient : IKafkaRequestClient, IKafkaRequestClientProbe, IHostedService, IDisposable
{
    private static readonly TimeSpan ReplyInboxStartupBudget = TimeSpan.FromSeconds(15);
    private static readonly TimeSpan ReplyInboxRetryDelay = TimeSpan.FromSeconds(1);
    private static readonly TimeSpan ReplyInboxBootstrapProduceTimeout = TimeSpan.FromSeconds(5);
    private static readonly TimeSpan ReplyInboxRequestWaitBudget = TimeSpan.FromSeconds(5);

    private readonly IAdminClient _admin;
    private readonly IProducer<string, string> _producer;
    private readonly IConsumer<string, string> _consumer;
    private readonly ILogger<KafkaRequestClient> _log;
    private readonly string _replyInbox;
    private readonly object _replyInboxStateLock = new();

    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private TaskCompletionSource<bool> _replyInboxReady = CreateReplyInboxReadySignal();

    private CancellationTokenSource? _loopCts;
    private Task? _loopTask;
    private int _isReplyInboxReady;
    private string _replyInboxStatus = "gateway starting";

    public bool IsReplyInboxReady => Volatile.Read(ref _isReplyInboxReady) == 1;
    public string ReplyInbox => _replyInbox;
    public string ReplyInboxStatus => Volatile.Read(ref _replyInboxStatus);

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
        .SetPartitionsAssignedHandler((_, partitions) =>
        {
            if (!partitions.Any(tp => string.Equals(tp.Topic, _replyInbox, StringComparison.Ordinal)))
            {
                return;
            }

            MarkReplyInboxReady(partitions);
        })
        .SetPartitionsRevokedHandler((_, partitions) =>
        {
            if (!partitions.Any(tp => string.Equals(tp.Topic, _replyInbox, StringComparison.Ordinal)))
            {
                return;
            }

            ResetReplyInboxReady($"partitions revoked: {string.Join(", ", partitions)}");
        })
        .SetPartitionsLostHandler((_, partitions) =>
        {
            if (!partitions.Any(tp => string.Equals(tp.Topic, _replyInbox, StringComparison.Ordinal)))
            {
                return;
            }

            ResetReplyInboxReady($"partitions lost: {string.Join(", ", partitions)}");
        })
        .Build();
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        _loopCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _loopTask = Task.Run(() => BootstrapAndConsumeAsync(_loopCts.Token), CancellationToken.None);
        SetReplyInboxStatus("background bootstrap/consume loop starting");
        _log.LogInformation(
            "KafkaRequestClient starting background bootstrap/consume loop, reply inbox: {Inbox}",
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
        try
        {
            await WaitForReplyInboxReadyAsync(startedAt, timeout, ct);
        }
        catch (TimeoutException tex)
        {
            _log.LogWarning(tex,
                "KafkaRequest reply inbox not ready topic={Topic} replyInbox={ReplyInbox} timeoutMs={TimeoutMs} lastState={LastState}",
                topic,
                _replyInbox,
                (int)timeout.TotalMilliseconds,
                ReplyInboxStatus);
            throw;
        }

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
                if (!await PrepareReplyInboxAsync(ct))
                {
                    SetReplyInboxStatus("reply inbox topic bootstrap failed; retrying");
                    _log.LogWarning(
                        "KafkaRequestClient could not prepare reply inbox {Inbox}; retrying in {DelayMs} ms",
                        _replyInbox,
                        (int)ReplyInboxRetryDelay.TotalMilliseconds);
                    await Task.Delay(ReplyInboxRetryDelay, ct);
                    continue;
                }

                _consumer.Subscribe(_replyInbox);
                SetReplyInboxStatus("subscribed to reply inbox; waiting for partition assignment");
                _log.LogInformation(
                    "KafkaRequestClient subscribed to reply inbox {Inbox}; waiting for assignment",
                    _replyInbox);

                await ConsumeLoopAsync(ct);
                return;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                SetReplyInboxStatus($"bootstrap loop failed: {SummarizeException(ex)}");
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
        while (Volatile.Read(ref _isReplyInboxReady) == 0)
        {
            var remainingTimeout = timeout - Stopwatch.GetElapsedTime(startedAt);
            if (remainingTimeout <= TimeSpan.Zero)
            {
                throw new TimeoutException($"Kafka reply inbox '{_replyInbox}' is not ready. Last state: {ReplyInboxStatus}");
            }

            var readinessWaitBudget = remainingTimeout <= ReplyInboxRequestWaitBudget
                ? remainingTimeout
                : ReplyInboxRequestWaitBudget;

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeoutCts.CancelAfter(readinessWaitBudget);

            try
            {
                await GetReplyInboxReadyTask().WaitAsync(timeoutCts.Token);
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested)
            {
                throw new TimeoutException(
                    $"Kafka reply inbox '{_replyInbox}' is not ready after waiting {(int)readinessWaitBudget.TotalMilliseconds} ms. Last state: {ReplyInboxStatus}");
            }
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
                SetReplyInboxStatus($"consume error {cex.Error.Code}: {cex.Error.Reason}");
                _log.LogDebug("Consume error: {Code} {Reason}", cex.Error.Code, cex.Error.Reason);
                await Task.Delay(500, ct);
                continue;
            }
            catch (Exception ex)
            {
                SetReplyInboxStatus($"consume loop exception: {SummarizeException(ex)}");
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
        try { _admin.Dispose(); } catch (ObjectDisposedException) { }
        try { _producer.Dispose(); } catch (ObjectDisposedException) { }
        try { _consumer.Dispose(); } catch (ObjectDisposedException) { }
        _loopCts?.Dispose();
    }

    private async Task<bool> PrepareReplyInboxAsync(CancellationToken ct)
    {
        if (!await EnsureReplyInboxTopicAsync(ct))
        {
            return await TryBootstrapReplyInboxViaProduceAsync(ct);
        }

        // Seed one marker record immediately so an idle but healthy inbox no
        // longer looks like an empty orphan to the periodic janitor sweep.
        await TryBootstrapReplyInboxViaProduceAsync(ct);
        return true;
    }

    private async Task<bool> EnsureReplyInboxTopicAsync(CancellationToken ct)
    {
        var deadline = DateTime.UtcNow + ReplyInboxStartupBudget;
        SetReplyInboxStatus("ensuring reply inbox topic exists");

        _log.LogInformation(
            "KafkaRequestClient ensuring reply inbox topic {Inbox} startupBudgetMs={StartupBudgetMs}",
            _replyInbox,
            (int)ReplyInboxStartupBudget.TotalMilliseconds);

        while (DateTime.UtcNow < deadline)
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
                SetReplyInboxStatus("reply inbox topic created; waiting for subscription");
                await Task.Delay(TimeSpan.FromMilliseconds(500), ct);
                return true;
            }
            catch (CreateTopicsException ex) when (ex.Results.All(r => r.Error.Code == ErrorCode.TopicAlreadyExists))
            {
                _log.LogDebug("KafkaRequestClient reply inbox topic already exists: {Inbox}", _replyInbox);
                SetReplyInboxStatus("reply inbox topic already exists; waiting for subscription");
                await Task.Delay(TimeSpan.FromMilliseconds(500), ct);
                return true;
            }
            catch (Exception ex)
            {
                SetReplyInboxStatus($"reply inbox topic create failed: {SummarizeException(ex)}");
                _log.LogWarning(ex,
                    "KafkaRequestClient failed to create reply inbox {Inbox}; retrying",
                    _replyInbox);
                await Task.Delay(ReplyInboxRetryDelay, ct);
            }
        }

        _log.LogWarning(
            "KafkaRequestClient could not confirm reply inbox topic {Inbox} within startup budget; continuing with best-effort subscribe",
            _replyInbox);
        SetReplyInboxStatus("reply inbox topic create exceeded startup budget; trying bootstrap produce");
        return false;
    }

    private async Task<bool> TryBootstrapReplyInboxViaProduceAsync(CancellationToken ct)
    {
        var bootstrapPayload = JsonSerializer.Serialize(new
        {
            kind = "reply_inbox_bootstrap",
            reply_inbox = _replyInbox,
            ts_utc = DateTime.UtcNow,
        });

        using var produceCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        produceCts.CancelAfter(ReplyInboxBootstrapProduceTimeout);
        SetReplyInboxStatus("bootstrap-producing to reply inbox topic");

        try
        {
            await _producer.ProduceAsync(
                _replyInbox,
                new Message<string, string>
                {
                    Key = "reply-inbox-bootstrap",
                    Value = bootstrapPayload,
                },
                produceCts.Token);

            _log.LogInformation(
                "KafkaRequestClient bootstrap-produced reply inbox topic {Inbox} timeoutMs={TimeoutMs}",
                _replyInbox,
                (int)ReplyInboxBootstrapProduceTimeout.TotalMilliseconds);
            SetReplyInboxStatus("reply inbox bootstrap-produced; waiting for subscription");
            return true;
        }
        catch (Exception ex) when (ex is not OperationCanceledException || !ct.IsCancellationRequested)
        {
            SetReplyInboxStatus($"bootstrap produce failed: {SummarizeException(ex)}");
            _log.LogWarning(ex,
                "KafkaRequestClient could not bootstrap reply inbox topic {Inbox} via producer",
                _replyInbox);
            return false;
        }
    }

    private static TaskCompletionSource<bool> CreateReplyInboxReadySignal() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private Task GetReplyInboxReadyTask()
    {
        lock (_replyInboxStateLock)
        {
            return _replyInboxReady.Task;
        }
    }

    private void MarkReplyInboxReady(IReadOnlyList<TopicPartition> partitions)
    {
        TaskCompletionSource<bool> readySignal;
        var becameReady = Interlocked.Exchange(ref _isReplyInboxReady, 1) == 0;
        var partitionsText = string.Join(", ", partitions);

        lock (_replyInboxStateLock)
        {
            readySignal = _replyInboxReady;
        }

        readySignal.TrySetResult(true);
        SetReplyInboxStatus($"assigned to partitions: {partitionsText}");

        if (becameReady)
        {
            _log.LogInformation(
                "KafkaRequestClient reply inbox assigned inbox={Inbox} partitions={Partitions}",
                _replyInbox,
                partitionsText);
        }
    }

    private void ResetReplyInboxReady(string reason)
    {
        var wasReady = Interlocked.Exchange(ref _isReplyInboxReady, 0) == 1;
        TaskCompletionSource<bool>? previousSignal = null;

        lock (_replyInboxStateLock)
        {
            if (!wasReady && !_replyInboxReady.Task.IsCompleted)
            {
                return;
            }

            previousSignal = _replyInboxReady;
            _replyInboxReady = CreateReplyInboxReadySignal();
        }

        if (wasReady)
        {
            SetReplyInboxStatus($"reply inbox lost readiness: {reason}");
            _log.LogWarning(
                "KafkaRequestClient reply inbox reset inbox={Inbox} reason={Reason}",
                _replyInbox,
                reason);
        }
        else if (previousSignal is not null && previousSignal.Task.IsCompleted)
        {
            SetReplyInboxStatus($"reply inbox waiting again: {reason}");
            _log.LogDebug(
                "KafkaRequestClient reply inbox signal refreshed inbox={Inbox} reason={Reason}",
                _replyInbox,
                reason);
        }
    }

    private void SetReplyInboxStatus(string status)
    {
        Interlocked.Exchange(ref _replyInboxStatus, status);
    }

    private static string SummarizeException(Exception ex) =>
        ex.Message.Replace(Environment.NewLine, " ").Trim();
}
