using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Market;

public interface IMarketServiceClient
{
    Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default);
    Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default);
}
