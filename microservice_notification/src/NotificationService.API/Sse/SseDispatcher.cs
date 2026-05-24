using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using NotificationService.Application.Interfaces;
using NotificationService.Domain.Entities;

namespace NotificationService.API.Sse;

public sealed class SseClient
{
    public required HttpResponse Response { get; init; }
    public required CancellationToken Token { get; init; }
    public required Guid UserId { get; init; }
}

public sealed class SseDispatcher : ISseDispatcher
{
    private readonly ConcurrentDictionary<Guid, List<SseClient>> _clients = new();
    private readonly ILogger<SseDispatcher> _log;
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public SseDispatcher(ILogger<SseDispatcher> log) => _log = log;

    public void Register(SseClient client)
    {
        var list = _clients.GetOrAdd(client.UserId, _ => new List<SseClient>());
        lock (list) list.Add(client);
    }

    public void Unregister(SseClient client)
    {
        if (!_clients.TryGetValue(client.UserId, out var list)) return;
        lock (list) list.Remove(client);
    }

    public async Task PushAsync(Guid userId, Notification n)
    {
        if (!_clients.TryGetValue(userId, out var list)) return;

        SseClient[] snapshot;
        lock (list) snapshot = list.ToArray();
        if (snapshot.Length == 0) return;

        var payload = JsonSerializer.Serialize(new
        {
            id = n.Id,
            kind = n.Kind,
            title = n.Title,
            body = n.Body,
            deeplink = n.Deeplink,
            createdAt = n.CreatedAt,
        }, Json);

        var bytes = Encoding.UTF8.GetBytes($"event: notification\ndata: {payload}\n\n");

        foreach (var c in snapshot)
        {
            if (c.Token.IsCancellationRequested) continue;
            try
            {
                await c.Response.Body.WriteAsync(bytes, c.Token);
                await c.Response.Body.FlushAsync(c.Token);
            }
            catch (Exception ex)
            {
                _log.LogDebug(ex, "SSE write failed for user {UserId}", userId);
                Unregister(c);
            }
        }
    }
}
