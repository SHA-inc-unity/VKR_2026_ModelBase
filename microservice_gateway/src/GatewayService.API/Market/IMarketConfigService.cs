using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

/// <summary>
/// Returns the static market configuration consumed by Kotlin/Flutter clients
/// to populate symbol/timeframe pickers and validate candle count inputs.
/// </summary>
public interface IMarketConfigService
{
    /// <summary>
    /// Returns the market configuration response for the given exchange.
    /// When <paramref name="exchange"/> is null or empty, defaults to bybit.
    /// The symbol list is sourced from Market Watcher's live tracked-symbols
    /// per exchange (so the dropdown always matches what MW persists in DB),
    /// with a fallback to the Bybit instrument list when MW is unavailable.
    /// The result is cached in Redis per exchange.
    /// </summary>
    Task<MarketConfigResponse> GetConfigAsync(string? exchange = null, CancellationToken ct = default);

    /// <summary>
    /// Returns true when <paramref name="symbol"/> is in the active symbol list
    /// for the given exchange (fast path — uses the symbol cache, not the full config).
    /// Falls back to the built-in fallback list when the cache is empty.
    /// </summary>
    Task<bool> IsKnownSymbolAsync(string symbol, string? exchange = null, CancellationToken ct = default);
}
