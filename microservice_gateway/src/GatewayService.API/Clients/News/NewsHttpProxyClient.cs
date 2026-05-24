using System.Text;
using System.Text.Json;
using GatewayService.API.Clients.Social;

namespace GatewayService.API.Clients.News;

public sealed class NewsHttpProxyClient : INewsHttpProxyClient
{
    private readonly HttpClient _http;
    public NewsHttpProxyClient(HttpClient http) => _http = http;

    public async Task<DownstreamResponse> ForwardAsync(
        HttpMethod method,
        string path,
        string? query = null,
        JsonElement? body = null,
        CancellationToken ct = default)
    {
        var pathAndQuery = string.IsNullOrEmpty(query) ? path : $"{path}?{query}";
        using var req = new HttpRequestMessage(method, pathAndQuery);

        if (body.HasValue && body.Value.ValueKind != JsonValueKind.Undefined)
        {
            req.Content = new StringContent(body.Value.GetRawText(), Encoding.UTF8, "application/json");
        }

        using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        var content = await resp.Content.ReadAsStringAsync(ct);
        var contentType = resp.Content.Headers.ContentType?.ToString() ?? "application/json";
        return new DownstreamResponse((int)resp.StatusCode, content, contentType);
    }
}
