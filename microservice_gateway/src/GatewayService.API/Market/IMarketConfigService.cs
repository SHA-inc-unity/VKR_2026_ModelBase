using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

/// <summary>
/// Returns the static market configuration consumed by Kotlin clients
/// to populate symbol/timeframe pickers and validate candle count inputs.
/// </summary>
public interface IMarketConfigService
{
    /// <summary>
    /// Returns the market configuration response.
    /// The result is cached in Redis for <c>MarketSettings.ConfigCacheTtlSeconds</c>.
    /// The symbol list is refreshed from Bybit in the background every
    /// <c>MarketSettings.SymbolsCacheTtlSeconds</c>.
    /// </summary>
    Task<MarketConfigResponse> GetConfigAsync(CancellationToken ct = default);

    /// <summary>
    /// Returns true when <paramref name="symbol"/> is in the active symbol list
    /// (fast path — uses the symbol cache, not the full config).
    /// Falls back to the built-in fallback list when the cache is empty.
    /// </summary>
    Task<bool> IsKnownSymbolAsync(string symbol, CancellationToken ct = default);
}
