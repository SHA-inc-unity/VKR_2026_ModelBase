using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Market;

public interface IMarketServiceClient
{
    Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default);
    Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default);
    Task<ServiceResult<PublicMarketOverviewResponse>> GetPublicOverviewAsync(int trendingLimit = 5, CancellationToken ct = default);
    Task<ServiceResult<MarketTickersResponse>> GetTickersAsync(
        int page = 1,
        int pageSize = 25,
        string? search = null,
        string? sortBy = null,
        string? sortDir = null,
        IReadOnlyList<string>? symbols = null,
        string? collection = null,
        CancellationToken ct = default);
    Task<ServiceResult<MarketBatchQuotesResponse>> GetQuotesAsync(IReadOnlyList<string> symbols, CancellationToken ct = default);
    Task<ServiceResult<MarketConverterQuoteResponse>> GetConverterQuoteAsync(string fromAsset, string toAsset, decimal amount, CancellationToken ct = default);
}
