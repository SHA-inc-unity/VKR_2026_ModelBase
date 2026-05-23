using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Frontend;

namespace GatewayService.API.Clients.Portfolio;

public sealed class PortfolioServiceClient : IPortfolioServiceClient
{
    private readonly IFrontendContractState _state;
    private readonly ILogger<PortfolioServiceClient> _logger;

    public PortfolioServiceClient(
        IFrontendContractState state,
        ILogger<PortfolioServiceClient> logger)
    {
        _state = state;
        _logger = logger;
    }

    public Task<ServiceResult<PortfolioSummaryDto>> GetSummaryAsync(string userId, CancellationToken ct = default)
    {
        _logger.LogDebug("Portfolio service fallback is active; returning gateway-local summary for {UserId}", userId);
        return Task.FromResult(ServiceResult<PortfolioSummaryDto>.Ok(_state.GetDashboardPortfolioSummary(userId)));
    }

    public Task<ServiceResult<PortfolioDetailedSummaryResponse>> GetDetailedSummaryAsync(string userId, CancellationToken ct = default)
    {
        _logger.LogDebug("Portfolio detailed summary fallback is active; returning gateway-local summary for {UserId}", userId);
        return Task.FromResult(ServiceResult<PortfolioDetailedSummaryResponse>.Ok(_state.GetPortfolioSummary(userId)));
    }
}
