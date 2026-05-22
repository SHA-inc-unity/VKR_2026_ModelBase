namespace GatewayService.API.DTOs.Responses;

/// <summary>
/// Aggregated dashboard response for the Flutter main screen.
/// Supports partial/degraded state — sections may be null if their downstream is unavailable.
/// </summary>
public sealed record DashboardResponse
{
    public PortfolioSummaryDto? Portfolio { get; init; }
    public MarketOverviewDto? MarketOverview { get; init; }
    public IReadOnlyList<TrendingAssetDto> TrendingAssets { get; init; } = [];
    public IReadOnlyList<NewsTeaserDto> LatestNews { get; init; } = [];
    public DashboardMetaDto Meta { get; init; } = new();
}

public sealed record PortfolioSummaryDto
{
    public decimal TotalValueUsd { get; init; }
    public decimal PnlPercent24h { get; init; }
    public int AssetCount { get; init; }
}

public sealed record MarketOverviewDto
{
    public decimal BtcDominance { get; init; }
    public decimal TotalMarketCapUsd { get; init; }
    public decimal Volume24hUsd { get; init; }
}

public sealed record TrendingAssetDto
{
    public string Symbol { get; init; } = string.Empty;
    public decimal PriceUsd { get; init; }
    public decimal ChangePercent24h { get; init; }
}

public sealed record NewsTeaserDto
{
    public string Title { get; init; } = string.Empty;
    public string Source { get; init; } = string.Empty;
    public DateTimeOffset PublishedAt { get; init; }
    public string? ImageUrl { get; init; }
}

public sealed record DashboardMetaDto
{
    /// <summary>Sections absent from the response because downstream was unavailable.</summary>
    public IReadOnlyList<string> DegradedSections { get; init; } = [];
    public DateTimeOffset GeneratedAt { get; init; } = DateTimeOffset.UtcNow;
}
