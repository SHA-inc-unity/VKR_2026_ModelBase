using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace GatewayService.API.Clients.Social;

public sealed class SocialServiceClient : ISocialServiceClient
{
    private readonly HttpClient _http;

    public SocialServiceClient(HttpClient http) => _http = http;

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
}
