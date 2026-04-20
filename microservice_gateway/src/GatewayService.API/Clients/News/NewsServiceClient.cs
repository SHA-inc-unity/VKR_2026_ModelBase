using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.News;

/// <summary>Stub — News Service is not yet implemented.</summary>
public sealed class NewsServiceClient : INewsServiceClient
{
    private readonly ILogger<NewsServiceClient> _logger;

    public NewsServiceClient(ILogger<NewsServiceClient> logger) => _logger = logger;

    public Task<ServiceResult<IReadOnlyList<NewsItemDto>>> GetLatestAsync(int limit = 20, CancellationToken ct = default)
    {
        _logger.LogDebug("News service is not yet available; returning stub failure");
        return Task.FromResult(ServiceResult<IReadOnlyList<NewsItemDto>>.Fail("News service not yet implemented"));
    }
}
