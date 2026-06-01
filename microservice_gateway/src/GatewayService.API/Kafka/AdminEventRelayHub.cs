using System.Collections.Concurrent;
using System.Text.Json;
using System.Threading.Channels;
using Confluent.Kafka;
using Confluent.Kafka.Admin;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Kafka;

/// <summary>
/// Relays backend EVT_* events to the admin head over SSE.
///
/// Why this exists
/// ───────────────
/// In split deployment the admin Next.js process runs on a different host and
/// cannot reach the backend Redpanda broker (the broker advertises an internal
/// address and the external listener is unauthenticated). The gateway, however,
/// already lives inside the broker network. This hub runs a single Kafka
/// consumer subscribed to all <see cref="AdminTopics.AllEvents"/> topics and
/// fans every message out to all currently-connected admin SSE clients
/// (see <c>AdminController.Events</c>). The admin reverse-proxies that stream
/// to the browser using the logged-in admin user's own JWT — no Redpanda
/// credential is ever exposed off-host.
///
/// One Kafka consumer regardless of how many admin tabs are open: SSE clients
/// are cheap in-process channels, not extra consumer groups.
/// </summary>
public sealed class AdminEventRelayHub : IHostedService, IDisposable
{
    private const int SubscriberQueueCapacity = 256;

    private readonly IConsumer<string, string> _consumer;
    private readonly IAdminClient _admin;
    private readonly ILogger<AdminEventRelayHub> _log;
    private readonly ConcurrentDictionary<Guid, Channel<string>> _subscribers = new();

    private CancellationTokenSource? _loopCts;
    private Task? _loopTask;

    public AdminEventRelayHub(IOptions<KafkaSettings> opts, ILogger<AdminEventRelayHub> log)
    {
        _log = log;
        var bootstrap = opts.Value.BootstrapServers;

        _admin = new AdminClientBuilder(new AdminClientConfig
        {
            BootstrapServers = bootstrap,
        }).Build();

        _consumer = new ConsumerBuilder<string, string>(new ConsumerConfig
        {
            BootstrapServers               = bootstrap,
            // Unique group per gateway process so this consumer always receives
            // the full EVT_* stream rather than competing for partitions.
            GroupId                        = $"gateway-admin-events-{Guid.NewGuid():N}",
            AutoOffsetReset                = AutoOffsetReset.Latest,
            EnableAutoCommit               = true,
            AllowAutoCreateTopics          = false,
            TopicMetadataRefreshIntervalMs = 5000,
        })
        .SetErrorHandler((_, err) =>
        {
            if (err.IsFatal)
                _log.LogError("AdminEventRelay Kafka fatal: {Code} {Reason}", err.Code, err.Reason);
            else
                _log.LogDebug("AdminEventRelay Kafka non-fatal: {Code} {Reason}", err.Code, err.Reason);
        })
        .Build();
    }

    /// <summary>Active SSE client count — for diagnostics/logging.</summary>
    public int SubscriberCount => _subscribers.Count;

    /// <summary>
    /// Register an SSE client. Returns a reader over already-serialized event
    /// lines (<c>{"type":"&lt;topic&gt;","payload":&lt;json&gt;}</c>). Always call
    /// <see cref="Unsubscribe"/> when the client disconnects.
    /// </summary>
    public ChannelReader<string> Subscribe(out Guid id)
    {
        id = Guid.NewGuid();
        var channel = Channel.CreateBounded<string>(new BoundedChannelOptions(SubscriberQueueCapacity)
        {
            // A slow/stuck client must never block the consume loop or other
            // clients — drop its oldest queued events instead.
            FullMode     = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
            SingleWriter = false,
        });
        _subscribers[id] = channel;
        _log.LogInformation("AdminEventRelay client connected id={Id} total={Total}", id, _subscribers.Count);
        return channel.Reader;
    }

    public void Unsubscribe(Guid id)
    {
        if (_subscribers.TryRemove(id, out var channel))
        {
            channel.Writer.TryComplete();
            _log.LogInformation("AdminEventRelay client disconnected id={Id} total={Total}", id, _subscribers.Count);
        }
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        _loopCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _loopTask = Task.Run(() => RunAsync(_loopCts.Token), CancellationToken.None);
        _log.LogInformation(
            "AdminEventRelayHub starting; subscribing to {Count} EVT_* topics",
            AdminTopics.AllEvents.Length);
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
        foreach (var kv in _subscribers)
        {
            kv.Value.Writer.TryComplete();
        }
        try { _consumer.Close(); } catch { /* ignore */ }
    }

    private async Task RunAsync(CancellationToken ct)
    {
        await EnsureEventTopicsAsync(ct);

        while (!ct.IsCancellationRequested)
        {
            try
            {
                _consumer.Subscribe(AdminTopics.AllEvents);
                ConsumeLoop(ct);
                return;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "AdminEventRelay consume loop failed; retrying in 1s");
                try { await Task.Delay(TimeSpan.FromSeconds(1), ct); }
                catch (OperationCanceledException) { return; }
            }
        }
    }

    private void ConsumeLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            ConsumeResult<string, string>? result;
            try
            {
                result = _consumer.Consume(TimeSpan.FromMilliseconds(250));
            }
            catch (OperationCanceledException) { break; }
            catch (ConsumeException cex)
            {
                _log.LogDebug("AdminEventRelay consume error {Code} {Reason}", cex.Error.Code, cex.Error.Reason);
                continue;
            }

            if (result?.Message?.Value is null) continue;

            string line;
            try
            {
                // Forward the same shape the admin's kafkajs hub produced:
                // { type: <topic>, payload: <raw message value> }. The browser's
                // useEvents() consumes exactly this, so the admin can pipe it
                // through verbatim.
                using var doc = JsonDocument.Parse(result.Message.Value);
                line = JsonSerializer.Serialize(new
                {
                    type    = result.Topic,
                    payload = doc.RootElement,
                });
            }
            catch (JsonException)
            {
                // Non-JSON event value — skip rather than poison the stream.
                continue;
            }

            foreach (var kv in _subscribers)
            {
                kv.Value.Writer.TryWrite(line);
            }
        }
    }

    /// <summary>
    /// Pre-create the EVT_* topics. Redpanda + a fresh consumer can otherwise
    /// fail to subscribe to a not-yet-existing topic; the data/analitic
    /// producers create them lazily on first publish, so seed them here too.
    /// </summary>
    private async Task EnsureEventTopicsAsync(CancellationToken ct)
    {
        try
        {
            await _admin.CreateTopicsAsync(
                AdminTopics.AllEvents.Select(t => new TopicSpecification
                {
                    Name              = t,
                    NumPartitions     = 1,
                    ReplicationFactor = 1,
                }),
                new CreateTopicsOptions
                {
                    RequestTimeout   = TimeSpan.FromSeconds(10),
                    OperationTimeout = TimeSpan.FromSeconds(10),
                });
            _log.LogInformation("AdminEventRelay ensured {Count} EVT_* topics exist", AdminTopics.AllEvents.Length);
        }
        catch (CreateTopicsException ex) when (ex.Results.All(r => r.Error.Code is ErrorCode.TopicAlreadyExists or ErrorCode.NoError))
        {
            // All topics already exist — fine.
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            // Shutdown during startup — fine.
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "AdminEventRelay could not pre-create EVT_* topics; continuing (producers create them lazily)");
        }
    }

    public void Dispose()
    {
        try { _admin.Dispose(); } catch (ObjectDisposedException) { }
        try { _consumer.Dispose(); } catch (ObjectDisposedException) { }
        _loopCts?.Dispose();
    }
}
