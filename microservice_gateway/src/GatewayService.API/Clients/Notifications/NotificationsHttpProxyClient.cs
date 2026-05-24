using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using GatewayService.API.Clients.Social;

namespace GatewayService.API.Clients.Notifications;

public sealed class NotificationsHttpProxyClient : INotificationsHttpProxyClient
{
    private readonly HttpClient _http;
    public NotificationsHttpProxyClient(HttpClient http) => _http = http;

    public async Task<DownstreamResponse> ForwardAsync(
        HttpMethod method,
        string path,
        string? query = null,
        JsonElement? body = null,
        string? bearerToken = null,
        CancellationToken ct = default)
    {
        var pathAndQuery = string.IsNullOrEmpty(query) ? path : $"{path}?{query}";
        using var req = new HttpRequestMessage(method, pathAndQuery);

        if (body.HasValue && body.Value.ValueKind != JsonValueKind.Undefined)
        {
            req.Content = new StringContent(body.Value.GetRawText(), Encoding.UTF8, "application/json");
        }

        if (!string.IsNullOrWhiteSpace(bearerToken))
        {
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
        }

        using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        var content = await resp.Content.ReadAsStringAsync(ct);
        var contentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json";
        return new DownstreamResponse((int)resp.StatusCode, content, contentType);
    }

    public async Task<HttpResponseMessage> OpenStreamAsync(string pathAndQuery, string? bearerToken, CancellationToken ct)
    {
        var req = new HttpRequestMessage(HttpMethod.Get, pathAndQuery);
        if (!string.IsNullOrWhiteSpace(bearerToken))
        {
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
        }
        req.Headers.Accept.ParseAdd("text/event-stream");
        return await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
    }
}
