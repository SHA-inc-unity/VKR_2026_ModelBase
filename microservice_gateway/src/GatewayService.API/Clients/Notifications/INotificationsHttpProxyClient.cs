using System.Text.Json;
using GatewayService.API.Clients.Social;

namespace GatewayService.API.Clients.Notifications;

public interface INotificationsHttpProxyClient
{
    Task<DownstreamResponse> ForwardAsync(
        HttpMethod method,
        string path,
        string? query = null,
        JsonElement? body = null,
        string? bearerToken = null,
        CancellationToken ct = default);

    /// <summary>Opens a long-lived response for SSE; the caller streams the body downstream.</summary>
    Task<HttpResponseMessage> OpenStreamAsync(string pathAndQuery, string? bearerToken, CancellationToken ct);
}
