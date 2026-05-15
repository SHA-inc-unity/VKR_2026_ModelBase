namespace GatewayService.API.Market;

/// <summary>
/// Fetches the list of active linear USDT perpetual trading instruments
/// from the Bybit REST API.
/// </summary>
public interface IBybitSymbolProvider
{
    /// <summary>
    /// Returns the sorted list of active, USDT-settled, LinearPerpetual symbols
    /// from Bybit. Returns an empty list (never throws) when Bybit is unreachable.
    /// </summary>
    Task<IReadOnlyList<string>> GetActiveSymbolsAsync(CancellationToken ct = default);
}
