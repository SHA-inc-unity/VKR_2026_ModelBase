using System.Text.Json;
using AccountService.Application.Common.Exceptions;
using AccountService.Application.Interfaces.Repositories;
using Confluent.Kafka;
using Microsoft.Extensions.Options;

namespace AccountService.API.Kafka;

/// <summary>
/// BackgroundService consuming cmd.account.* Kafka topics.
/// Pattern mirrors microservice_data: consume → JSON envelope → dispatch → reply.
/// </summary>
public sealed class KafkaConsumerService : BackgroundService
{
    private readonly IConsumer<string, string> _consumer;
    private readonly KafkaProducer             _producer;
    private readonly IServiceScopeFactory      _scopeFactory;
    private readonly ILogger<KafkaConsumerService> _log;

    private readonly SemaphoreSlim _concurrency = new(16, 16);

    public KafkaConsumerService(
        IOptions<KafkaSettings> opts,
        KafkaProducer producer,
        IServiceScopeFactory scopeFactory,
        ILogger<KafkaConsumerService> log)
    {
        _producer     = producer;
        _scopeFactory = scopeFactory;
        _log          = log;

        var cfg = new ConsumerConfig
        {
            BootstrapServers               = opts.Value.BootstrapServers,
            GroupId                        = "microservice_account",
            AutoOffsetReset                = AutoOffsetReset.Earliest,
            EnableAutoCommit               = true,
            AllowAutoCreateTopics          = true,
            TopicMetadataRefreshIntervalMs = 5000,
        };
        _consumer = new ConsumerBuilder<string, string>(cfg)
            .SetErrorHandler((_, err) =>
            {
                if (err.IsFatal)
                    _log.LogError("Kafka fatal error: {Code} {Reason}", err.Code, err.Reason);
                else
                    _log.LogDebug("Kafka non-fatal: {Code} {Reason}", err.Code, err.Reason);
            })
            .Build();
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _log.LogInformation("KafkaConsumerService (account) started, topics: {Topics}",
            string.Join(", ", Topics.AllConsumed));

        // Yield immediately so BackgroundService.StartAsync returns and Host.StartAsync
        // can continue to the next hosted service (Kestrel HTTP binding). Without this,
        // the synchronous Consume loop below blocks the startup thread indefinitely when
        // all subscribed topics already exist and no messages are waiting.
        await Task.Yield();

        await SubscribeWithRetryAsync(stoppingToken);

        while (!stoppingToken.IsCancellationRequested)
        {
            ConsumeResult<string, string>? result = null;
            try
            {
                result = _consumer.Consume(TimeSpan.FromMilliseconds(200));
            }
            catch (OperationCanceledException) { break; }
            catch (ConsumeException cex) when (IsTransientConsumeError(cex.Error))
            {
                _log.LogDebug("Transient Kafka consume error: {Code} {Reason}",
                    cex.Error.Code, cex.Error.Reason);
                await Task.Delay(1000, stoppingToken);
                continue;
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Kafka consume error");
                await Task.Delay(1000, stoppingToken);
                continue;
            }

            if (result is null) continue;

            _ = Task.Run(async () =>
            {
                await _concurrency.WaitAsync(stoppingToken);
                JsonDocument? doc = null;
                try
                {
                    doc = JsonDocument.Parse(result.Message.Value);
                    var root          = doc.RootElement;
                    var correlationId = root.TryGetProperty("correlation_id", out var cid) ? cid.GetString() ?? "" : "";
                    var replyTo       = root.TryGetProperty("reply_to", out var rt) ? rt.GetString() ?? "" : "";
                    var payload       = root.TryGetProperty("payload", out var p) ? p : default;

                    await DispatchAsync(result.Topic, correlationId, replyTo, payload, stoppingToken);
                }
                catch (Exception ex)
                {
                    _log.LogError(ex, "Handler error on topic {Topic}", result.Topic);
                }
                finally
                {
                    doc?.Dispose();
                    _concurrency.Release();
                }
            }, stoppingToken);
        }

        _consumer.Close();
    }

    private async Task SubscribeWithRetryAsync(CancellationToken ct)
    {
        var attempt = 0;
        while (!ct.IsCancellationRequested)
        {
            try
            {
                _consumer.Subscribe(Topics.AllConsumed);
                _log.LogInformation("Subscribed to {Count} Kafka topics", Topics.AllConsumed.Length);
                return;
            }
            catch (Exception ex)
            {
                attempt++;
                var delay = TimeSpan.FromSeconds(Math.Min(30, Math.Pow(2, attempt)));
                _log.LogWarning(ex, "Subscribe failed (attempt {Attempt}); retrying in {Delay}s",
                    attempt, delay.TotalSeconds);
                try { await Task.Delay(delay, ct); } catch (OperationCanceledException) { return; }
            }
        }
    }

    private static bool IsTransientConsumeError(Error err) =>
        !err.IsFatal && (
            err.Code == ErrorCode.UnknownTopicOrPart ||
            err.Code == ErrorCode.Local_UnknownTopic ||
            err.Code == ErrorCode.Local_UnknownPartition ||
            err.Code == ErrorCode.LeaderNotAvailable ||
            err.Code == ErrorCode.NotCoordinatorForGroup ||
            err.Code == ErrorCode.GroupLoadInProgress);

    private async Task DispatchAsync(
        string topic, string correlationId, string replyTo,
        JsonElement payload, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(replyTo))
        {
            _log.LogWarning("Message on {Topic} has no reply_to, skipping", topic);
            return;
        }

        object response = topic switch
        {
            Topics.CmdAccountHealth  => HandleHealth(),
            Topics.CmdAccountGetUser => await HandleGetUserAsync(payload, ct),
            _                        => new { error = $"Unknown topic: {topic}" },
        };

        await _producer.PublishReplyAsync(replyTo, correlationId, response, ct);
    }

    // ── Handlers ──────────────────────────────────────────────────────────────

    private static object HandleHealth() => new
    {
        status  = "ok",
        service = "microservice_account",
        version = "1.0.0",
    };

    private async Task<object> HandleGetUserAsync(JsonElement payload, CancellationToken ct)
    {
        var userIdStr = TryGetString(payload, "user_id");
        if (string.IsNullOrWhiteSpace(userIdStr) || !Guid.TryParse(userIdStr, out var userId))
            return new { error = "invalid_user_id" };

        using var scope = _scopeFactory.CreateScope();
        var userRepo = scope.ServiceProvider.GetRequiredService<IUserRepository>();

        try
        {
            var user = await userRepo.GetByIdWithRolesAsync(userId, ct);
            if (user is null) return new { error = "not_found" };

            // GetByIdWithRolesAsync already eagerly loads UserRoles → Role via
            // Include/ThenInclude, so we can project role codes directly from
            // the navigation property — no second DB roundtrip needed.
            var roles = user.UserRoles.Select(ur => ur.Role.Code).ToList();

            return new
            {
                id         = user.Id,
                email      = user.Email,
                username   = user.Username,
                status     = user.Status.ToString(),
                roles,
                created_at = user.CreatedAt,
            };
        }
        catch (UserNotFoundException)
        {
            return new { error = "not_found" };
        }
    }

    private static string? TryGetString(JsonElement p, string name)
    {
        if (p.ValueKind != JsonValueKind.Object) return null;
        return p.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()
            : null;
    }

    public override void Dispose()
    {
        _consumer.Dispose();
        base.Dispose();
    }
}
