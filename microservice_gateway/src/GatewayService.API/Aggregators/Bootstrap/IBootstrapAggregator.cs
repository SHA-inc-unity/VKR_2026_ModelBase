using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Aggregators.Bootstrap;

public interface IBootstrapAggregator
{
    /// <param name="bearerToken">Nullable — bootstrap works for unauthenticated requests too.</param>
    Task<BootstrapResponse> AggregateAsync(string? bearerToken, CancellationToken ct = default);
}
