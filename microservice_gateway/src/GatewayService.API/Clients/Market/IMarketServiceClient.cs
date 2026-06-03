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
        string? category = null,
        CancellationToken ct = default);
    /// Canonical curated category ("sector") list with a live count of currently
    /// tracked snapshot tickers per category. Categories are a static map
    /// (CoinCategoryMap); only the per-category count is snapshot-derived.
    Task<ServiceResult<MarketCategoriesResponse>> GetCategoriesAsync(CancellationToken ct = default);
    Task<ServiceResult<MarketBatchQuotesResponse>> GetQuotesAsync(IReadOnlyList<string> symbols, CancellationToken ct = default);
    Task<ServiceResult<MarketRealtimeQuotesResponse>> GetRealtimeQuotesAsync(IReadOnlyList<string> symbols, string? exchange = null, CancellationToken ct = default);
    Task<ServiceResult<MarketConverterQuoteResponse>> GetConverterQuoteAsync(
        string fromAsset,
        string toAsset,
        decimal amount,
        string? exchange = null,
        CancellationToken ct = default);
    /// Per-exchange aggregate (count, gainers, losers, total volume,
    /// average change, sentiment) computed across the full MW-tracked
    /// symbol universe. Cached server-side so the client can render
    /// the Global Stats card without holding 17+ tickers in memory.
    Task<ServiceResult<MarketGlobalSummaryResponse>> GetGlobalSummaryAsync(
        string? exchange = null,
        CancellationToken ct = default);
}
