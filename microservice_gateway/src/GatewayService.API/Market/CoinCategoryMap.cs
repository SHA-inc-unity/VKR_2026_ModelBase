namespace GatewayService.API.Market;

/// <summary>
/// Curated, static <c>base-asset → category slugs</c> map for the gateway's
/// tracked symbol universe (the ~92 pairs returned by
/// <c>GET /api/v1/market/config</c>).
///
/// <para>
/// This is <b>our own curated data</b> — there is <b>no live CoinGecko / Bybit /
/// external call</b>. Categories ("sectors") are a hand-maintained classification
/// mirroring the <see cref="CoinGeckoIdMap"/> pattern (same file structure, same
/// case-insensitive base keys, same accessor-helper style, same soft "unmapped →
/// empty" contract). A coin may belong to <c>0..N</c> categories; an unmapped base
/// simply carries no category and still appears under the "All" view.
/// </para>
///
/// <para>
/// The frontend localizes by <c>slug</c>; <see cref="DisplayName"/> is a neutral
/// English label only (no translation responsibility lives here).
/// </para>
/// </summary>
public static class CoinCategoryMap
{
    /// <summary>One curated category ("sector") definition.</summary>
    /// <param name="Slug">Stable machine slug the frontend localizes by.</param>
    /// <param name="DisplayName">Neutral English fallback label.</param>
    public sealed record Category(string Slug, string DisplayName);

    /// <summary>
    /// Canonical category list. Order is the suggested display order; the frontend
    /// is free to reorder. Slugs are the stable contract — display names are only a
    /// neutral English fallback.
    /// </summary>
    private static readonly IReadOnlyList<Category> CategoryList = new[]
    {
        new Category("layer1", "Layer 1"),
        new Category("layer2", "Layer 2"),
        new Category("defi", "DeFi"),
        new Category("ai", "AI & Big Data"),
        new Category("meme", "Meme"),
        new Category("rwa", "Real World Assets"),
        new Category("staking", "Staking & Liquid Staking"),
        new Category("solana", "Solana Ecosystem"),
        new Category("exchange", "Exchange Tokens"),
        new Category("stable", "Stablecoins"),
        new Category("gaming", "Gaming & NFT"),
        new Category("oracle", "Oracle"),
    };

    /// <summary>
    /// Curated <c>base → category slugs</c> map (0..N slugs per base), hand-classified
    /// for OUR tracked universe (bases sourced from <see cref="CoinGeckoIdMap"/> and
    /// <c>GET /api/v1/market/config</c>). Every slug used below exists in
    /// <see cref="CategoryList"/>. Bases not present here intentionally carry no
    /// category (graceful "All-only") rather than a guessed one.
    /// </summary>
    private static readonly IReadOnlyDictionary<string, string[]> Map = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase)
    {
        // Majors / Layer 1 base chains.
        ["BTC"] = ["layer1"],
        ["ETH"] = ["layer1"],
        ["BNB"] = ["layer1", "exchange"],
        ["SOL"] = ["layer1", "solana"],
        ["ADA"] = ["layer1"],
        ["AVAX"] = ["layer1"],
        ["DOT"] = ["layer1"],
        ["ATOM"] = ["layer1"],
        ["NEAR"] = ["layer1", "ai"],
        ["APT"] = ["layer1"],
        ["SUI"] = ["layer1"],
        ["SEI"] = ["layer1"],
        ["TON"] = ["layer1"],
        ["TRX"] = ["layer1"],
        ["XRP"] = ["layer1"],
        ["XLM"] = ["layer1"],
        ["LTC"] = ["layer1"],
        ["BCH"] = ["layer1"],
        ["HBAR"] = ["layer1"],
        ["ICP"] = ["layer1"],
        ["KAS"] = ["layer1"],
        ["FIL"] = ["layer1"],
        ["INJ"] = ["layer1", "defi"],
        ["TIA"] = ["layer1"],
        ["WAVES"] = ["layer1"],
        ["XDC"] = ["layer1", "rwa"],
        ["IP"] = ["layer1", "rwa"],
        ["FOGO"] = ["layer1"],
        ["MON"] = ["layer1"],
        ["XAN"] = ["layer1"],
        ["CC"] = ["layer1"],
        ["NIGHT"] = ["layer1"],

        // Layer 2 / scaling.
        ["OP"] = ["layer2"],
        ["ARB"] = ["layer2"],
        ["POL"] = ["layer2"],
        ["STRK"] = ["layer2"],
        ["MNT"] = ["layer2"],
        ["ALT"] = ["layer2"],
        ["PLUME"] = ["layer2", "rwa"],

        // DeFi.
        ["UNI"] = ["defi"],
        ["AAVE"] = ["defi"],
        ["AERO"] = ["defi"],
        ["ENA"] = ["defi", "stable"],
        ["JUP"] = ["defi", "solana"],
        ["FIDA"] = ["defi", "solana"],
        ["CPOOL"] = ["defi", "rwa"],
        ["HYPE"] = ["defi"],
        ["ASTER"] = ["defi"],
        ["AVNT"] = ["defi"],
        ["APEX"] = ["defi"],
        ["AVL"] = ["defi"],
        ["VVV"] = ["defi", "ai"],

        // AI & big data.
        ["FET"] = ["ai"],
        ["RENDER"] = ["ai"],
        ["WLD"] = ["ai"],
        ["GRASS"] = ["ai", "solana"],
        ["ATH"] = ["ai"],
        ["VIRTUAL"] = ["ai"],
        ["HOLO"] = ["ai", "solana"],
        ["SAHARA"] = ["ai"],
        ["OPG"] = ["ai"],
        ["RECALL"] = ["ai"],
        ["ZEREBRO"] = ["ai", "solana", "meme"],

        // Meme.
        ["DOGE"] = ["meme"],
        ["SHIB"] = ["meme"],
        ["PEPE"] = ["meme"],
        ["WIF"] = ["meme", "solana"],
        ["POPCAT"] = ["meme", "solana"],
        ["TRUMP"] = ["meme", "solana"],
        ["SPX"] = ["meme"],
        ["BASED"] = ["meme"],
        ["PUMP"] = ["meme", "solana"],
        ["PENGU"] = ["meme", "solana", "gaming"],

        // Real-world assets.
        ["ONDO"] = ["rwa"],
        ["WLFI"] = ["rwa", "defi"],
        ["XPL"] = ["rwa", "stable"],

        // Staking / liquid staking.
        ["ETHFI"] = ["staking", "defi"],
        ["EIGEN"] = ["staking"],
        ["JTO"] = ["staking", "solana"],

        // Exchange / CeFi tokens.
        ["NEXO"] = ["exchange", "defi"],
        ["ME"] = ["exchange", "solana"],
        ["ZBT"] = ["exchange"],

        // Gaming / NFT.
        ["TOWNS"] = ["gaming"],
        ["CHZ"] = ["gaming"],

        // Oracle.
        ["LINK"] = ["oracle"],

        // Privacy / other established L1.
        ["ZEN"] = ["layer1"],
    };

    /// <summary>The canonical category list (slug + neutral English display name).</summary>
    public static IReadOnlyList<Category> Categories => CategoryList;

    /// <summary>All canonical category slugs, in display order.</summary>
    public static IReadOnlyList<string> AllSlugs { get; } =
        CategoryList.Select(category => category.Slug).ToArray();

    /// <summary>The full curated base→slugs map (read-only, case-insensitive on the base).</summary>
    public static IReadOnlyDictionary<string, string[]> Entries => Map;

    private static readonly string[] Empty = Array.Empty<string>();

    /// <summary>
    /// Resolves a base asset (e.g. <c>BTC</c>) to its curated category slugs, or an
    /// <b>empty</b> list (never <c>null</c>) when the base is unmapped — keeping the
    /// snapshot/ticker overlay null-ref free under <c>Nullable enable</c>.
    /// </summary>
    public static IReadOnlyList<string> CategoriesFor(string? baseAsset)
    {
        if (string.IsNullOrWhiteSpace(baseAsset))
        {
            return Empty;
        }

        return Map.TryGetValue(baseAsset.Trim(), out var slugs) ? slugs : Empty;
    }
}
