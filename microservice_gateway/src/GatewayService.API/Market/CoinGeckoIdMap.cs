namespace GatewayService.API.Market;

/// <summary>
/// Curated <c>base-asset → CoinGecko coin id</c> map for the gateway's tracked
/// symbol universe (the ~92 pairs returned by <c>GET /api/v1/market/config</c>).
///
/// <para>
/// A bare ticker→id lookup is <b>unsafe</b>: CoinGecko lists thousands of coins
/// that collide on the same ticker (e.g. <c>ETH</c> matches 13 coins, <c>PEPE</c>
/// 17, <c>SOL</c> 12 — most of them bridged/pegged/meme imposters). Each entry
/// below was hand-picked as the canonical, highest-cap asset for that ticker and
/// verified against <c>/coins/markets</c> to return a real
/// <c>circulating_supply</c> + <c>market_cap</c>.
/// </para>
///
/// <para>
/// Bases not present here resolve to <c>null</c> metadata and degrade gracefully
/// (the ticker shows a null market cap / supply rather than a wrong value) — the
/// same soft-fail contract used elsewhere in the market client.
/// </para>
/// </summary>
public static class CoinGeckoIdMap
{
    /// <summary>
    /// Bases intentionally left unmapped because no confident canonical id exists
    /// (ambiguous ticker, not a clearly-listed coin, or pre-launch): documented so
    /// the gap is a deliberate degrade, not an oversight. As of the curation pass
    /// these were: EDGE, CHIP, BLEND, MEGA, H.
    /// </summary>
    private static readonly Dictionary<string, string> Map = new(StringComparer.OrdinalIgnoreCase)
    {
        ["AAVE"] = "aave",
        ["ADA"] = "cardano",
        ["AERO"] = "aerodrome-finance",
        ["ALT"] = "altlayer",
        ["APEX"] = "apex-token-2",
        ["APT"] = "aptos",
        ["ARB"] = "arbitrum",
        ["ASTER"] = "aster-2",
        ["ATH"] = "aethir",
        ["ATOM"] = "cosmos",
        ["AVAX"] = "avalanche-2",
        ["AVL"] = "avalon-2",
        ["AVNT"] = "avantis",
        ["BASED"] = "based-2",
        ["BCH"] = "bitcoin-cash",
        ["BNB"] = "binancecoin",
        ["BTC"] = "bitcoin",
        ["CC"] = "canton-network",
        ["CHZ"] = "chiliz",
        ["CPOOL"] = "clearpool",
        ["DOGE"] = "dogecoin",
        ["DOT"] = "polkadot",
        ["EIGEN"] = "eigenlayer",
        ["ENA"] = "ethena",
        ["ETHFI"] = "ether-fi",
        ["ETH"] = "ethereum",
        ["FET"] = "fetch-ai",
        ["FIDA"] = "bonfida",
        ["FIL"] = "filecoin",
        ["FOGO"] = "fogo",
        ["GRASS"] = "grass",
        ["HBAR"] = "hedera-hashgraph",
        ["HOLO"] = "holoworld",
        ["HYPE"] = "hyperliquid",
        ["ICP"] = "internet-computer",
        ["INJ"] = "injective-protocol",
        ["IP"] = "story-2",
        ["JTO"] = "jito-governance-token",
        ["JUP"] = "jupiter-exchange-solana",
        ["KAS"] = "kaspa",
        ["LINK"] = "chainlink",
        ["LTC"] = "litecoin",
        ["ME"] = "magic-eden",
        ["MNT"] = "mantle",
        ["MON"] = "monad",
        ["NEAR"] = "near",
        ["NEXO"] = "nexo",
        ["NIGHT"] = "midnight",
        ["ONDO"] = "ondo-finance",
        ["OPG"] = "opengradient",
        ["OP"] = "optimism",
        ["PENGU"] = "pudgy-penguins",
        ["PEPE"] = "pepe",
        ["PLUME"] = "plume",
        ["POL"] = "polygon-ecosystem-token",
        ["POPCAT"] = "popcat",
        ["PUMP"] = "pump-fun",
        ["RECALL"] = "recall",
        ["RENDER"] = "render-token",
        ["SAHARA"] = "sahara-ai",
        ["SEI"] = "sei-network",
        ["SHIB"] = "shiba-inu",
        ["SOL"] = "solana",
        ["SPX"] = "spx6900",
        ["STRK"] = "starknet",
        ["SUI"] = "sui",
        ["TIA"] = "celestia",
        ["TON"] = "the-open-network",
        ["TOWNS"] = "towns",
        ["TRUMP"] = "official-trump",
        ["TRX"] = "tron",
        ["UNI"] = "uniswap",
        ["VIRTUAL"] = "virtual-protocol",
        ["VVV"] = "venice-token",
        ["WAVES"] = "waves",
        ["WIF"] = "dogwifcoin",
        ["WLD"] = "worldcoin-wld",
        ["WLFI"] = "world-liberty-financial",
        ["XAN"] = "anoma",
        ["XDC"] = "xdce-crowd-sale",
        ["XLM"] = "stellar",
        ["XPL"] = "plasma",
        ["XRP"] = "ripple",
        ["ZBT"] = "zerobase",
        ["ZEN"] = "zencash",
        ["ZEREBRO"] = "zerebro",
    };

    /// <summary>All curated CoinGecko ids (deduplicated), for a batch metadata fetch.</summary>
    public static IReadOnlyCollection<string> AllCoinGeckoIds { get; } =
        Map.Values.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();

    /// <summary>The full curated base→id map (read-only, case-insensitive on the base).</summary>
    public static IReadOnlyDictionary<string, string> Entries => Map;

    /// <summary>
    /// Resolves a base asset (e.g. <c>BTC</c>) to its curated CoinGecko id,
    /// or <c>null</c> when the base is unmapped (graceful degrade).
    /// </summary>
    public static string? Resolve(string? baseAsset)
    {
        if (string.IsNullOrWhiteSpace(baseAsset))
        {
            return null;
        }

        return Map.TryGetValue(baseAsset.Trim(), out var id) ? id : null;
    }
}
