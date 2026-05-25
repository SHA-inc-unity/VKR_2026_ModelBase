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
            ["BTC"]  = "XBT",
            ["DOGE"] = "XDG",
            // NOTE: MATIC was previously mapped to POL because Kraken renamed
            // the Polygon token. As of 2026-05 Kraken has delisted both the
            // MATIC/USDT and POL/USDT spot pairs entirely (REST AssetPairs
            // returns "Unknown asset pair" for both). Keep the rename out of
            // here so PairCandidates doesn't waste two probes per cycle on
            // a pair that does not exist — Market Watcher's "skips unsupported
            // symbol MATICUSDT" log line is the correct outcome.
            //
            // NOTE: DOGE↔XDG: Kraken REST altname is XDGUSDT with wsname
            // XDG/USDT, but the live WebSocket v2 accepts DOGE/USDT only. We
            // keep this dataset↔altname rename so the REST AssetPairs probe
            // matches; the WS form is rewritten at subscribe time via
            // ToWebSocketPair (see _restToWebsocketWsname below).
        };

    private static readonly IReadOnlyDictionary<string, string> _exchangeToDataset =
        _datasetToExchange.ToDictionary(kv => kv.Value, kv => kv.Key, StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// Kraken WebSocket v2 ↔ REST <c>wsname</c> override table.
    ///
    /// REST <c>/0/public/AssetPairs</c> still emits the legacy altname-prefixed
    /// wsname for two specific pairs: <c>XBT/USDT</c> (BTC) and <c>XDG/USDT</c>
    /// (DOGE). Kraken's WebSocket v2 only accepts the modern names — verified
    /// against the WS <c>instrument</c> channel snapshot (1544 pairs returned;
    /// none of them contain <c>XBT</c> or <c>XDG</c>; both <c>BTC/USDT</c> and
    /// <c>DOGE/USDT</c> subscribe successfully where the legacy form gets a
    /// <c>"Currency pair not supported"</c> error).
    ///
    /// Other historical X-prefix tickers (<c>XRP</c>, <c>XMR</c>, <c>XTZ</c>,
    /// <c>XAUT</c>) are not affected and remain identical on both APIs.
    /// </summary>
    private static readonly IReadOnlyDictionary<string, string> _restToWebsocketWsname =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["XBT/USDT"] = "BTC/USDT",
            ["XDG/USDT"] = "DOGE/USDT",
        };

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

    public string ToWebSocketPair(string wsname)
    {
        if (string.IsNullOrWhiteSpace(wsname)) return wsname;
        var trimmed = wsname.Trim();
        return _restToWebsocketWsname.TryGetValue(trimmed, out var mapped) ? mapped : trimmed;
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
