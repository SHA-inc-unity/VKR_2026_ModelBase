using System.Text.Json;
using GatewayService.API.Clients.Social;

namespace GatewayService.API.Clients.News;

public interface INewsHttpProxyClient
{
    Task<DownstreamResponse> ForwardAsync(
        HttpMethod method,
        string path,
        string? query = null,
        JsonElement? body = null,
        CancellationToken ct = default);
}
