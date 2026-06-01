using DataService.API.Bybit;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Markets;

/// <summary>
/// Resolves the <see cref="IMarketDataClient"/> for a given exchange. Which
/// implementation backs each exchange is decided once at construction from
/// <c>DataService:Dataset:OhlcvProvider</c>:
/// <list type="bullet">
///   <item><c>"ccxt"</c> (default) — the unified <see cref="CcxtMarketDataClient"/>
///   is primary for every exchange it is configured for; the native
///   Bybit/Binance adapters fill any exchange ccxt is not configured for.</item>
///   <item><c>"native"</c> — the hand-written Bybit/Binance adapters are primary;
///   ccxt only fills exchanges the native adapters do not implement (i.e. any
///   new exchange added purely via ccxt config).</item>
/// </list>
/// Either way, dataset ingest, the job runner, repository, table naming, export
/// and Kafka contracts are untouched — they all go through this one seam.
/// </summary>
public sealed class MarketDataClientFactory
{
    private readonly IReadOnlyDictionary<string, IMarketDataClient> _clients;

    /// <summary>Exchanges that the live <c>MarketWatcherService</c> can subscribe
    /// to over websockets (those have a dedicated Binance.Net/Bybit.Net WS path).
    /// Dataset ingest can support more exchanges than this via ccxt.</summary>
    public static readonly IReadOnlySet<string> SupportedExchanges =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "bybit",
            "binance",
        };

    public MarketDataClientFactory(
        BybitApiClient bybit,
        BinanceApiClient binance,
        IEnumerable<CcxtMarketDataClient> ccxtClients,
        IOptions<DataServiceSettings> settings,
        ILogger<MarketDataClientFactory> log)
    {
        var native = new Dictionary<string, IMarketDataClient>(StringComparer.OrdinalIgnoreCase)
        {
            [bybit.Exchange]   = bybit,
            [binance.Exchange] = binance,
        };

        var ccxt = new Dictionary<string, IMarketDataClient>(StringComparer.OrdinalIgnoreCase);
        foreach (var c in ccxtClients) ccxt[c.Exchange] = c;

        var provider = (settings.Value.Dataset.OhlcvProvider ?? "ccxt").Trim().ToLowerInvariant();
        var merged = new Dictionary<string, IMarketDataClient>(StringComparer.OrdinalIgnoreCase);

        if (provider == "native")
        {
            // native primary; ccxt fills exchanges native does not implement.
            foreach (var kv in ccxt)   merged[kv.Key] = kv.Value;
            foreach (var kv in native) merged[kv.Key] = kv.Value;
        }
        else
        {
            // "ccxt" (default): ccxt primary; native fills exchanges ccxt is not
            // configured for.
            foreach (var kv in native) merged[kv.Key] = kv.Value;
            foreach (var kv in ccxt)   merged[kv.Key] = kv.Value;
        }

        _clients = merged;
        log.LogInformation(
            "MarketDataClientFactory: OhlcvProvider={Provider}; clients=[{Clients}]",
            provider,
            string.Join(", ", _clients.Select(kv => $"{kv.Key}->{kv.Value.GetType().Name}")));
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
