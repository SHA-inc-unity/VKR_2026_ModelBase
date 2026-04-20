using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Aggregators.Dashboard;

public interface IDashboardAggregator
{
    Task<DashboardResponse> AggregateAsync(string userId, CancellationToken ct = default);
}
