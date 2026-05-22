using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.News;

public interface INewsServiceClient
{
    Task<ServiceResult<IReadOnlyList<NewsItemDto>>> GetLatestAsync(int limit = 20, CancellationToken ct = default);
}
