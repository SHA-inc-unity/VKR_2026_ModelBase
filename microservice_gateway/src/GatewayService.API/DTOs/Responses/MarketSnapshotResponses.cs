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

public sealed record MarketTickerItemDto
{
    public string Symbol { get; init; } = string.Empty;
    public string DisplayName { get; init; } = string.Empty;
    public string BaseAsset { get; init; } = string.Empty;
    public string QuoteAsset { get; init; } = string.Empty;
    public decimal Price { get; init; }
    public decimal Change24h { get; init; }
    public decimal Volume24h { get; init; }
    public decimal? MarketCap { get; init; }
    public decimal High24h { get; init; }
    public decimal Low24h { get; init; }
    public int Rank { get; init; }
    public string? LogoUrl { get; init; }
    public int ExchangeCount { get; init; }
    public DateTimeOffset UpdatedAt { get; init; } = DateTimeOffset.UtcNow;
    public bool IsTrending { get; init; }
}

public sealed record MarketBatchQuotesResponse
{
    public string SnapshotId { get; init; } = string.Empty;
    public IReadOnlyList<MarketQuoteDto> Items { get; init; } = [];
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