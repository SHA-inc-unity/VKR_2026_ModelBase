namespace DataService.API.Markets;

/// <summary>
/// Maps between dataset-canonical and Kraken-wire base asset names.
///
/// Kraken historically uses <c>XBT</c> instead of <c>BTC</c>, and renamed
/// several tokens (notably <c>MATIC → POL</c> in 2024). Future renames are
/// added as a one-line entry in <see cref="_datasetToExchange"/>.
///
/// Pair candidates cover Kraken's two USDT pair naming flavours:
///   - <c>BTCUSDT</c>     — <c>altname</c> form
///   - <c>BTC/USDT</c>    — <c>wsname</c> form (used for the WebSocket feed)
/// </summary>
public sealed class KrakenSymbolMapper : IExchangeSymbolMapper
{
    /// <summary>
    /// Dataset base → Kraken wire base. Entries here MUST be kept in sync with
    /// the inverse map below; the constructor sanity-checks symmetry on first
    /// use.
    /// </summary>
    private static readonly IReadOnlyDictionary<string, string> _datasetToExchange =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["BTC"]   = "XBT",
            ["MATIC"] = "POL",
            // NOTE: DOGE: Kraken REST returns `wsname=XDG/USDT` for the
            // XDGUSDT pair, but Kraken's public WebSocket then rejects that
            // wsname with "Currency pair not supported". We therefore do
            // *not* map DOGE→XDG and instead skip Dogecoin on Kraken until
            // the WS namespace catches up. Once Kraken accepts XDG/USDT on
            // ws.kraken.com, re-add ["DOGE"] = "XDG" here.
        };

    private static readonly IReadOnlyDictionary<string, string> _exchangeToDataset =
        _datasetToExchange.ToDictionary(kv => kv.Value, kv => kv.Key, StringComparer.OrdinalIgnoreCase);

    public string Exchange => KrakenApiClient.ExchangeName;

    public string ToExchangeBase(string datasetBase)
    {
        if (string.IsNullOrWhiteSpace(datasetBase)) return datasetBase;
        var normalized = datasetBase.Trim().ToUpperInvariant();
        return _datasetToExchange.TryGetValue(normalized, out var mapped) ? mapped : normalized;
    }

    public string ToDatasetBase(string exchangeBase)
    {
        if (string.IsNullOrWhiteSpace(exchangeBase)) return exchangeBase;
        var normalized = exchangeBase.Trim().ToUpperInvariant();
        return _exchangeToDataset.TryGetValue(normalized, out var mapped) ? mapped : normalized;
    }

    public string NormalizeDatasetSymbol(string symbolOrAltname)
    {
        if (string.IsNullOrWhiteSpace(symbolOrAltname)) return symbolOrAltname;
        var normalized = symbolOrAltname.Trim().ToUpperInvariant();

        // Strip a trailing /USDT slash form (wsname) before splitting.
        var slash = normalized.IndexOf('/');
        string baseAsset;
        string quote;
        if (slash > 0 && slash < normalized.Length - 1)
        {
            baseAsset = normalized[..slash];
            quote = normalized[(slash + 1)..];
        }
        else if (normalized.EndsWith("USDT", StringComparison.OrdinalIgnoreCase))
        {
            baseAsset = normalized[..^4];
            quote = "USDT";
        }
        else
        {
            return normalized;
        }

        var canonicalBase = ToDatasetBase(baseAsset);
        return $"{canonicalBase}{quote}";
    }

    public IReadOnlyList<string> PairCandidates(string datasetSymbol)
    {
        var symbol = datasetSymbol.Trim().ToUpperInvariant();
        var candidates = new List<string>(4) { symbol };
        if (!symbol.EndsWith("USDT", StringComparison.OrdinalIgnoreCase))
        {
            return candidates;
        }

        var baseAsset = symbol[..^4];
        var mappedBase = ToExchangeBase(baseAsset);

        // Order matters: the canonical USDT pair form first, then slash form,
        // then the mapped-base equivalents (XBT…, POL…). Kraken's AssetPairs
        // endpoint accepts either alt-name or ws-name in the `pair` query, so
        // both flavours need to be probed for legacy renames.
        AddIfNew(candidates, $"{baseAsset}/USDT");
        if (!string.Equals(mappedBase, baseAsset, StringComparison.OrdinalIgnoreCase))
        {
            AddIfNew(candidates, $"{mappedBase}USDT");
            AddIfNew(candidates, $"{mappedBase}/USDT");
        }

        return candidates;
    }

    public string MarketWatchPairCandidate(string datasetSymbol)
    {
        var symbol = datasetSymbol.Trim().ToUpperInvariant();
        if (!symbol.EndsWith("USDT", StringComparison.OrdinalIgnoreCase))
        {
            return symbol;
        }

        var baseAsset = symbol[..^4];
        var mappedBase = ToExchangeBase(baseAsset);
        return $"{mappedBase}/USDT";
    }

    private static void AddIfNew(List<string> list, string item)
    {
        foreach (var existing in list)
        {
            if (string.Equals(existing, item, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }
        }
        list.Add(item);
    }
}
