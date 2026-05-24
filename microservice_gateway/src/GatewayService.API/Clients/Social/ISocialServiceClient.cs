using System.Text.Json;

namespace GatewayService.API.Clients.Social;

public interface ISocialServiceClient
{
    Task<DownstreamResponse> ForwardAsync(
        HttpMethod method,
        string path,
        string? query = null,
        JsonElement? body = null,
        string? bearerToken = null,
        CancellationToken ct = default);
}

public sealed record DownstreamResponse(int StatusCode, string Content, string ContentType);
