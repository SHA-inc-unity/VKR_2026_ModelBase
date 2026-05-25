namespace DataService.API.Markets;

/// <summary>
/// Per-exchange translation between dataset-canonical symbols (BTC, MATIC, …)
/// and exchange-wire symbols (XBT, POL, …). Kraken's REST API in particular
/// uses several legacy / renamed base assets that diverge from the canonical
/// dataset universe; centralising the mapping here keeps `KrakenApiClient`
/// from sprouting ad-hoc `if (symbol == "BTC")` checks.
///
/// Bybit and Binance use canonical names directly and do not need a mapper.
/// </summary>
public interface IExchangeSymbolMapper
{
    /// <summary>Lower-case exchange key, e.g. "kraken". Matches
    /// <see cref="IMarketDataClient.Exchange"/> for the corresponding client.</summary>
    string Exchange { get; }

    /// <summary>Translate a dataset base asset to the exchange's wire name.
    /// Identity for unmapped tickers, e.g. <c>BTC → XBT</c>, <c>MATIC → POL</c>.</summary>
    string ToExchangeBase(string datasetBase);

    /// <summary>Translate an exchange wire base asset to the canonical dataset
    /// base, e.g. <c>XBT → BTC</c>, <c>POL → MATIC</c>. Identity for unmapped.</summary>
    string ToDatasetBase(string exchangeBase);

    /// <summary>Translate a dataset symbol (e.g. <c>BTCUSDT</c>) to the dataset-
    /// canonical form an exchange wire response should ultimately produce. Used
    /// to normalise altname / wsname responses from the exchange catalog.</summary>
    string NormalizeDatasetSymbol(string symbolOrAltname);

    /// <summary>Ordered list of pair-name candidates the exchange's
    /// <c>AssetPairs</c> / instruments endpoint will accept when probing a
    /// canonical dataset symbol. The first match wins.</summary>
    IReadOnlyList<string> PairCandidates(string datasetSymbol);

    /// <summary>The single pair candidate used when probing the streaming /
    /// market-watch endpoint (Kraken's wsname format requires a slash).</summary>
    string MarketWatchPairCandidate(string datasetSymbol);

    /// <summary>Translate a REST-advertised <c>wsname</c> into the form the
    /// exchange's live WebSocket actually accepts. On Kraken these two
    /// namespaces have diverged for a handful of pairs (notably
    /// <c>XBT/USDT → BTC/USDT</c> and <c>XDG/USDT → DOGE/USDT</c>): the REST
    /// <c>AssetPairs</c> endpoint still emits the legacy altname-prefixed
    /// wsname, but Kraken's WebSocket v2 only recognises the modern colloquial
    /// names. See the live verification documented in
    /// <c>https://docs.kraken.com/api/docs/websocket-v2/book</c>. Identity for
    /// any pair that doesn't need a rewrite, and identity by default for any
    /// mapper that hasn't opted in.</summary>
    string ToWebSocketPair(string wsname) => wsname;
}
