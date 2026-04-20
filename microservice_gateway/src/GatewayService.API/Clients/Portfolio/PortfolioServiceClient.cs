using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Portfolio;

/// <summary>
/// Stub — Portfolio Service is not yet implemented.
/// Returns a Fail result so the dashboard aggregator marks the section as degraded.
/// Replace with a real HTTP client when the service is available.
/// </summary>
public sealed class PortfolioServiceClient : IPortfolioServiceClient
{
    private readonly ILogger<PortfolioServiceClient> _logger;

    public PortfolioServiceClient(ILogger<PortfolioServiceClient> logger) => _logger = logger;

    public Task<ServiceResult<PortfolioSummaryDto>> GetSummaryAsync(string userId, CancellationToken ct = default)
    {
        _logger.LogDebug("Portfolio service is not yet available; returning stub failure");
        return Task.FromResult(ServiceResult<PortfolioSummaryDto>.Fail("Portfolio service not yet implemented"));
    }
}
