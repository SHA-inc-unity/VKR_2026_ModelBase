using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Portfolio;

public interface IPortfolioServiceClient
{
    Task<ServiceResult<PortfolioSummaryDto>> GetSummaryAsync(string userId, CancellationToken ct = default);
}
