using System.Text.Json;
using Confluent.Kafka;
using Microsoft.Extensions.Options;
using NotificationService.Application.Common.Settings;
using NotificationService.Application.Interfaces;
using NotificationService.Application.Services;
using NotificationService.Domain.Entities;

namespace NotificationService.API.Kafka;

public sealed class KafkaConsumerService : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly NotificationKafkaSettings _settings;
    private readonly ILogger<KafkaConsumerService> _log;

    public KafkaConsumerService(
        IServiceScopeFactory scopes,
        IOptions<NotificationKafkaSettings> opts,
        ILogger<KafkaConsumerService> log)
    {
        _scopes = scopes;
        _settings = opts.Value;
        _log = log;
    }

    protected override Task ExecuteAsync(CancellationToken stoppingToken)
    {
        return Task.Run(() => Run(stoppingToken), stoppingToken);
    }

    private async Task Run(CancellationToken ct)
    {
        var cfg = new ConsumerConfig
        {
            BootstrapServers = _settings.BootstrapServers,
            GroupId = _settings.GroupId,
            EnableAutoCommit = true,
            AutoOffsetReset = AutoOffsetReset.Latest,
        };

        using var consumer = new ConsumerBuilder<string, string>(cfg).Build();
        try
        {
            consumer.Subscribe(new[] { _settings.SocialEventsTopic, _settings.NewsEventsTopic });
            _log.LogInformation("Kafka consumer subscribed to {Topics}",
                string.Join(", ", new[] { _settings.SocialEventsTopic, _settings.NewsEventsTopic }));

            while (!ct.IsCancellationRequested)
            {
                try
                {
                    var cr = consumer.Consume(ct);
                    if (cr is null) continue;
                    await HandleAsync(cr.Topic, cr.Message.Value, ct);
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    _log.LogWarning(ex, "Kafka consume tick failed");
                    try { await Task.Delay(TimeSpan.FromSeconds(2), ct); }
                    catch (OperationCanceledException) { break; }
                }
            }
        }
        finally
        {
            try { consumer.Close(); } catch { /* ignore */ }
        }
    }

    private async Task HandleAsync(string topic, string body, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(body)) return;
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        var type = root.TryGetProperty("type", out var t) ? t.GetString() : null;
        if (string.IsNullOrEmpty(type)) return;
        if (!root.TryGetProperty("payload", out var payload)) return;

        using var scope = _scopes.CreateScope();
        var svc = scope.ServiceProvider.GetRequiredService<INotificationsAppService>();

        switch (type)
        {
            case "comment.created":
                await HandleCommentCreatedAsync(scope.ServiceProvider, svc, payload, ct);
                break;
            case "news.created":
                await HandleNewsCreatedAsync(scope.ServiceProvider, svc, payload, ct);
                break;
            // Other social events ignored for now.
        }
    }

    private async Task HandleCommentCreatedAsync(IServiceProvider sp, INotificationsAppService svc, JsonElement payload, CancellationToken ct)
    {
        if (!payload.TryGetProperty("parentId", out var p) || p.ValueKind != JsonValueKind.String) return;
        if (!Guid.TryParse(p.GetString(), out var parentId)) return;
        if (!payload.TryGetProperty("commentId", out var c) || !Guid.TryParse(c.GetString(), out var commentId)) return;

        Guid? authorId = null;
        if (payload.TryGetProperty("authorId", out var a) && Guid.TryParse(a.GetString(), out var ag)) authorId = ag;

        var targetType = payload.TryGetProperty("targetType", out var tt) ? tt.GetString() ?? string.Empty : string.Empty;
        var targetId = payload.TryGetProperty("targetId", out var ti) ? ti.GetString() ?? string.Empty : string.Empty;

        var social = sp.GetRequiredService<ISocialDirectoryService>();
        var recipient = await social.GetCommentAuthorAsync(parentId, ct);
        if (recipient is null) return;
        if (authorId is not null && recipient.Value == authorId.Value) return;

        var deeplink = targetType.Equals("news", StringComparison.OrdinalIgnoreCase)
            ? $"/news/{targetId}"
            : $"/asset/{targetId}";

        var n = Notification.Create(
            userId: recipient.Value,
            kind: "comment.reply",
            title: "Someone replied to your comment",
            body: "Tap to view the reply",
            deeplink: deeplink,
            payloadJson: JsonSerializer.Serialize(new { commentId, parentId, targetType, targetId }),
            dedupKey: commentId.ToString());

        await svc.PushAsync(n, ct);
    }

    private async Task HandleNewsCreatedAsync(IServiceProvider sp, INotificationsAppService svc, JsonElement payload, CancellationToken ct)
    {
        if (!payload.TryGetProperty("newsId", out var nid) || !Guid.TryParse(nid.GetString(), out var newsId)) return;
        var title = payload.TryGetProperty("title", out var t) ? t.GetString() ?? string.Empty : string.Empty;
        var tags = new List<string>();
        if (payload.TryGetProperty("tags", out var ts) && ts.ValueKind == JsonValueKind.Array)
        {
            foreach (var tag in ts.EnumerateArray())
            {
                if (tag.ValueKind == JsonValueKind.String)
                {
                    var s = tag.GetString();
                    if (!string.IsNullOrWhiteSpace(s)) tags.Add(s!.ToUpperInvariant());
                }
            }
        }
        if (tags.Count == 0) return;

        var social = sp.GetRequiredService<ISocialDirectoryService>();
        var recipients = new HashSet<Guid>();
        foreach (var tag in tags.Distinct())
        {
            var users = await social.GetFavoriteUsersBySymbolAsync(tag, ct);
            foreach (var u in users) recipients.Add(u);
        }
        if (recipients.Count == 0) return;

        foreach (var u in recipients)
        {
            var n = Notification.Create(
                userId: u,
                kind: "news.favorite",
                title: "News about your favorites",
                body: title,
                deeplink: $"/news/{newsId}",
                payloadJson: JsonSerializer.Serialize(new { newsId, tags }),
                dedupKey: newsId.ToString());
            await svc.PushAsync(n, ct);
        }
    }
}
