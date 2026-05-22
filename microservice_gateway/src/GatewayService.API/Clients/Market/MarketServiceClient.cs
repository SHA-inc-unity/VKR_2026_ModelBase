using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Market;

/// <summary>Stub — Market Data Service is not yet implemented.</summary>
public sealed class MarketServiceClient : IMarketServiceClient
{
    private readonly ILogger<MarketServiceClient> _logger;

    public MarketServiceClient(ILogger<MarketServiceClient> logger) => _logger = logger;

    public Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default)
    {
        _logger.LogDebug("Market service is not yet available; returning stub failure");
        return Task.FromResult(ServiceResult<MarketOverviewDto>.Fail("Market service not yet implemented"));
    }

    public Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default)
    {
        _logger.LogDebug("Market service is not yet available; returning stub failure");
        return Task.FromResult(ServiceResult<IReadOnlyList<TrendingAssetDto>>.Fail("Market service not yet implemented"));
    }
}
