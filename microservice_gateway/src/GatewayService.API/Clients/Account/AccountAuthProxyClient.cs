using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace GatewayService.API.Clients.Account;

public sealed class AccountAuthProxyClient : IAccountAuthProxyClient
{
    private readonly HttpClient _http;

    public AccountAuthProxyClient(HttpClient http) => _http = http;

    public async Task<AccountProxyResult> ForwardAsync(
        HttpMethod method,
        string path,
        JsonElement? body = null,
        string? bearerToken = null,
        CancellationToken ct = default)
    {
        using var request = new HttpRequestMessage(method, path);

        if (body.HasValue)
        {
            request.Content = new StringContent(
                body.Value.GetRawText(),
                Encoding.UTF8,
                "application/json");
        }

        if (!string.IsNullOrWhiteSpace(bearerToken))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
        }

        using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        var content = await response.Content.ReadAsStringAsync(ct);
        var contentType = response.Content.Headers.ContentType?.ToString() ?? "application/json";

        return new AccountProxyResult(
            (int)response.StatusCode,
            content,
            contentType);
    }
}