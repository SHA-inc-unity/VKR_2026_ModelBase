using DataService.API.Bybit;

namespace DataService.API.Markets;

public sealed class MarketDataClientFactory
{
    private readonly IReadOnlyDictionary<string, IMarketDataClient> _clients;

    public static readonly IReadOnlySet<string> SupportedExchanges =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "bybit",
            "binance",
            "kraken",
        };

    public MarketDataClientFactory(
        BybitApiClient bybit,
        BinanceApiClient binance,
        KrakenApiClient kraken)
    {
        _clients = new Dictionary<string, IMarketDataClient>(StringComparer.OrdinalIgnoreCase)
        {
            [bybit.Exchange] = bybit,
            [binance.Exchange] = binance,
            [kraken.Exchange] = kraken,
        };
    }

    public IMarketDataClient GetRequiredClient(string? exchange)
    {
        var normalized = string.IsNullOrWhiteSpace(exchange)
            ? "bybit"
            : exchange.Trim().ToLowerInvariant();

        if (_clients.TryGetValue(normalized, out var client)) return client;
        throw new ArgumentException($"unsupported exchange: {normalized}");
    }

    public static bool IsSupportedExchange(string? exchange)
    {
        var normalized = string.IsNullOrWhiteSpace(exchange)
            ? "bybit"
            : exchange.Trim().ToLowerInvariant();
        return SupportedExchanges.Contains(normalized);
    }
}