using System.Net;
using System.Text.Json;
using NotificationService.Application.Interfaces;

namespace NotificationService.API.Services;

public sealed class HttpSocialDirectoryService : ISocialDirectoryService
{
    private readonly HttpClient _client;
    private readonly ILogger<HttpSocialDirectoryService> _log;
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public HttpSocialDirectoryService(HttpClient client, ILogger<HttpSocialDirectoryService> log)
    {
        _client = client;
        _log = log;
    }

    public async Task<Guid?> GetCommentAuthorAsync(Guid commentId, CancellationToken ct)
    {
        try
        {
            using var resp = await _client.GetAsync($"/internal/comments/{commentId}/author", ct);
            if (resp.StatusCode == HttpStatusCode.NotFound) return null;
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogWarning("Social returned {Status} for comment {Id}", (int)resp.StatusCode, commentId);
                return null;
            }
            var body = await resp.Content.ReadAsStringAsync(ct);
            var doc = JsonDocument.Parse(body);
            if (doc.RootElement.TryGetProperty("authorId", out var aid) && aid.ValueKind == JsonValueKind.String)
            {
                return Guid.TryParse(aid.GetString(), out var g) ? g : null;
            }
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to resolve comment author {Id}", commentId);
        }
        return null;
    }

    public async Task<IReadOnlyList<Guid>> GetFavoriteUsersBySymbolAsync(string symbol, CancellationToken ct)
    {
        try
        {
            using var resp = await _client.GetAsync($"/internal/favorites/users-by-symbol/{Uri.EscapeDataString(symbol)}", ct);
            if (!resp.IsSuccessStatusCode) return Array.Empty<Guid>();
            var body = await resp.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(body);
            if (!doc.RootElement.TryGetProperty("users", out var users) || users.ValueKind != JsonValueKind.Array)
                return Array.Empty<Guid>();

            var result = new List<Guid>();
            foreach (var u in users.EnumerateArray())
            {
                if (u.ValueKind == JsonValueKind.String && Guid.TryParse(u.GetString(), out var g))
                    result.Add(g);
            }
            return result;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to resolve favorite users for {Symbol}", symbol);
            return Array.Empty<Guid>();
        }
    }
}
