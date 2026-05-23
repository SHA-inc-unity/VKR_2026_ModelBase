using System.Net.Http;
using System.Text.Json;

namespace GatewayService.API.Clients.Account;

public interface IAccountAuthProxyClient
{
    Task<AccountProxyResult> ForwardAsync(
        HttpMethod method,
        string path,
        JsonElement? body = null,
        string? bearerToken = null,
        CancellationToken ct = default);
}

public sealed record AccountProxyResult(
    int StatusCode,
    string Content,
    string ContentType);