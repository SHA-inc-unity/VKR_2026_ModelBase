namespace GatewayService.API.DTOs.Responses;

public sealed record MarketTickersResponse
{
    public string SnapshotId { get; init; } = string.Empty;
    public string Collection { get; init; } = "market";
    public IReadOnlyList<MarketTickerItemDto> Items { get; init; } = [];
    public int Total { get; init; }
    public int Page { get; init; }
    public int PageSize { get; init; }
    public string? Search { get; init; }
    public string SortBy { get; init; } = "rank";
    public string SortDir { get; init; } = "desc";
    public FrontendResponseMetaDto Meta { get; init; } = new();
}

/// <summary>
/// Response body for <c>GET /api/v1/market/categories</c>: the canonical curated
/// category ("sector") list with a live count of how many CURRENTLY-tracked
/// snapshot tickers fall into each category. Lets the frontend render only
/// non-empty sectors (or grey out empty ones). Counts come from the live gateway
/// snapshot — categories themselves are static (no external call).
/// </summary>
public sealed record MarketCategoriesResponse
{
    public IReadOnlyList<MarketCategoryDto> Items { get; init; } = [];
}

public sealed record MarketCategoryDto
{
    /// <summary>Stable machine slug the frontend localizes by (e.g. <c>layer1</c>).</summary>
    public string Slug { get; init; } = string.Empty;

    /// <summary>Neutral English fallback label (frontend may override per-slug).</summary>
    public string DisplayName { get; init; } = string.Empty;

    /// <summary>Number of currently-tracked snapshot tickers carrying this category.</summary>
    public int Count { get; init; }
}

public sealed record MarketTickerItemDto
{
    public string Symbol { get; init; } = string.Empty;
    public string DisplayName { get; init; } = string.Empty;
    public string BaseAsset { get; init; } = string.Empty;
    public string QuoteAsset { get; init; } = string.Empty;
    public decimal Price { get; init; }
    public decimal Change24h { get; init; }
    public decimal Volume24h { get; init; }

    /// <summary>
    /// Real circulating-supply market cap (<c>circulatingSupply × livePrice</c>),
    /// or <c>null</c> when supply is unknown (base unmapped / CoinGecko miss).
    /// Replaces the former open-interest/turnover proxy.
    /// </summary>
    public decimal? MarketCap { get; init; }

    /// <summary>Circulating supply (coins in circulation), or <c>null</c> when unknown.</summary>
    public decimal? CirculatingSupply { get; init; }

    /// <summary>Total supply (minted, incl. locked), or <c>null</c> when unknown.</summary>
    public decimal? TotalSupply { get; init; }

    /// <summary>Max supply (hard cap), or <c>null</c> when uncapped/unknown.</summary>
    public decimal? MaxSupply { get; init; }

    /// <summary>
    /// Fully-diluted valuation (<c>(maxSupply ?? totalSupply) × livePrice</c>),
    /// or <c>null</c> when neither supply figure is known.
    /// </summary>
    public decimal? Fdv { get; init; }

    /// <summary>All-time-high price (USD) per CoinGecko, or <c>null</c> when unknown.</summary>
    public decimal? Ath { get; init; }

    /// <summary>
    /// 1 h price-change %, computed in the gateway from our own candle store
    /// (microservice_data), or <c>null</c> when there is no hourly candle ~1 h old.
    /// </summary>
    public decimal? Change1h { get; init; }

    /// <summary>
    /// 7 d price-change %, computed from our own daily candle store, or <c>null</c>
    /// when there is no daily candle at/older than 7 days.
    /// </summary>
    public decimal? Change7d { get; init; }

    /// <summary>
    /// 30 d price-change %, computed from our own daily candle store, or <c>null</c>
    /// when there is no daily candle at/older than 30 days.
    /// </summary>
    public decimal? Change30d { get; init; }

    public decimal High24h { get; init; }
    public decimal Low24h { get; init; }
    public int Rank { get; init; }
    public string? LogoUrl { get; init; }
    public int ExchangeCount { get; init; }
    public DateTimeOffset UpdatedAt { get; init; } = DateTimeOffset.UtcNow;
    public bool IsTrending { get; init; }

    /// <summary>
    /// Curated category ("sector") slugs (e.g. <c>["layer1","solana"]</c>) from the
    /// gateway's static <c>CoinCategoryMap</c> — OUR own data, no external call. A
    /// coin may carry <c>0..N</c> slugs; the array is empty (never null) for an
    /// unmapped base. The frontend localizes by slug and can filter the list via
    /// <c>GET /api/v1/market/tickers?category=&lt;slug&gt;</c>.
    /// </summary>
    public IReadOnlyList<string> Categories { get; init; } = [];
}

public sealed record MarketBatchQuotesResponse
{
    public string SnapshotId { get; init; } = string.Empty;
    public IReadOnlyList<MarketQuoteDto> Items { get; init; } = [];
    public IReadOnlyList<string> MissingSymbols { get; init; } = [];
    public FrontendResponseMetaDto Meta { get; init; } = new();
}

public sealed record MarketRealtimeQuotesResponse
{
    public IReadOnlyList<MarketRealtimeQuoteDto> Items { get; init; } = [];
    public IReadOnlyList<string> MissingSymbols { get; init; } = [];
    public FrontendResponseMetaDto Meta { get; init; } = new();
}

public sealed record MarketQuoteDto
{
    public string Symbol { get; init; } = string.Empty;
    public decimal Price { get; init; }
    public decimal Change24h { get; init; }
    public decimal High24h { get; init; }
    public decimal Low24h { get; init; }
    public decimal Volume24h { get; init; }
    public DateTimeOffset UpdatedAt { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record MarketRealtimeQuoteDto
{
    public string Symbol { get; init; } = string.Empty;
    public decimal Price { get; init; }
    public decimal Change24h { get; init; }
    public decimal High24h { get; init; }
    public decimal Low24h { get; init; }
    public decimal Volume24h { get; init; }
    public string? Exchange { get; init; }
    public string? RealtimeSymbol { get; init; }
    public long? LagMs { get; init; }
    public string Source { get; init; } = string.Empty;
    public bool IsFallback { get; init; }
    public DateTimeOffset UpdatedAt { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record MarketConverterQuoteResponse
{
    public string FromAsset { get; init; } = string.Empty;
    public string ToAsset { get; init; } = string.Empty;
    public decimal Amount { get; init; }
    public decimal Rate { get; init; }
    public decimal ConvertedAmount { get; init; }
    public string Source { get; init; } = string.Empty;
    public DateTimeOffset GeneratedAt { get; init; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedAt { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record MarketConvertResponse
{
    public string From { get; init; } = string.Empty;
    public string To { get; init; } = string.Empty;
    public decimal Amount { get; init; }
    public decimal Rate { get; init; }
    public decimal ConvertedAmount { get; init; }
    public string SourceLabel { get; init; } = string.Empty;
    public DateTimeOffset UpdatedAt { get; init; } = DateTimeOffset.UtcNow;
}