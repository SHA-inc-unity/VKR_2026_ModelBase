using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;

namespace GatewayService.API.Clients.Market;

public sealed class MarketServiceClient : IMarketServiceClient
{
    private readonly IMarketConfigService _marketConfig;
    private readonly ILogger<MarketServiceClient> _logger;

    public MarketServiceClient(
        IMarketConfigService marketConfig,
        ILogger<MarketServiceClient> logger)
    {
        _marketConfig = marketConfig;
        _logger = logger;
    }

    public Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default)
    {
        _logger.LogDebug("Market service fallback is active; returning gateway-local overview placeholders");
        return Task.FromResult(ServiceResult<MarketOverviewDto>.Ok(new MarketOverviewDto
        {
            BtcDominance = 0,
            TotalMarketCapUsd = 0,
            Volume24hUsd = 0,
        }));
    }

    public async Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default)
    {
        var config = await _marketConfig.GetConfigAsync(ct);
        var items = config.Symbols
            .Take(Math.Max(1, limit))
            .Select(symbol => new TrendingAssetDto
            {
                Symbol = symbol,
                PriceUsd = 0,
                ChangePercent24h = 0,
            })
            .ToArray();

        _logger.LogDebug("Market service fallback is active; returning {Count} gateway-local trending symbols", items.Length);
        return ServiceResult<IReadOnlyList<TrendingAssetDto>>.Ok(items);
    }
}
