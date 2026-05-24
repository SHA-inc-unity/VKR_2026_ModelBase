namespace GatewayService.API.DTOs.Responses;

public sealed record PublicMarketOverviewResponse
{
    public PublicMarketOverviewDto MarketOverview { get; init; } = new();
    public IReadOnlyList<string> TrendingAssets { get; init; } = [];
    public FrontendResponseMetaDto Meta { get; init; } = new();
}

public sealed record PublicMarketOverviewDto
{
    public decimal? TotalMarketCap { get; init; }
    public decimal? BtcDominance { get; init; }
    public decimal? Volume24h { get; init; }
    public int? ActiveAssets { get; init; }
    public int? FearGreedValue { get; init; }
    public string? FearGreedLabel { get; init; }
}

public sealed record FrontendResponseMetaDto
{
    public DateTimeOffset GeneratedAt { get; init; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? UpdatedAt { get; init; }
    public IReadOnlyList<string> DegradedSections { get; init; } = [];
    public IReadOnlyList<string> DegradedFields { get; init; } = [];
}

public sealed record PortfolioDetailedSummaryResponse
{
    public decimal TotalValue { get; init; }
    public decimal TotalPnl { get; init; }
    public decimal TotalPnlPercent { get; init; }
    public int AssetCount { get; init; }
    public int ExchangeCount { get; init; }
    public IReadOnlyList<PortfolioAssetSummaryDto> ByAsset { get; init; } = [];
    public IReadOnlyList<PortfolioExchangeSummaryDto> ByExchange { get; init; } = [];
}

public sealed record PortfolioAssetSummaryDto
{
    public string Symbol { get; init; } = string.Empty;
    public decimal TotalAmount { get; init; }
    public decimal TotalValue { get; init; }
    public decimal Change24h { get; init; }
    public IReadOnlyList<PortfolioAssetExchangeBreakdownDto> ExchangeBreakdown { get; init; } = [];
}

public sealed record PortfolioAssetExchangeBreakdownDto
{
    public string Exchange { get; init; } = string.Empty;
    public decimal Amount { get; init; }
    public decimal Value { get; init; }
}

public sealed record PortfolioExchangeSummaryDto
{
    public string Exchange { get; init; } = string.Empty;
    public decimal TotalValue { get; init; }
    public decimal Change24h { get; init; }
    public bool IsSynced { get; init; }
    public DateTimeOffset LastSyncedAt { get; init; }
    public IReadOnlyList<PortfolioExchangeHoldingDto> Holdings { get; init; } = [];
}

public sealed record PortfolioExchangeHoldingDto
{
    public string Symbol { get; init; } = string.Empty;
    public decimal Amount { get; init; }
    public decimal Value { get; init; }
    public decimal Change24h { get; init; }
}

public sealed record AvailableExchangeDto
{
    public string Id { get; init; } = string.Empty;
    public string Name { get; init; } = string.Empty;
    public string Slug { get; init; } = string.Empty;
    public string? LogoUrl { get; init; }
    public bool IsActive { get; init; }
    public bool IsConnected { get; init; }
}

public sealed record LinkedExchangeDto
{
    public string Name { get; init; } = string.Empty;
    public string Slug { get; init; } = string.Empty;
    public string MaskedKey { get; init; } = string.Empty;
    public decimal CachedBalance { get; init; }
    public bool IsActive { get; init; }
    public DateTimeOffset LinkedAt { get; init; }
}

public sealed record PriceAlertDto
{
    public string Id { get; init; } = string.Empty;
    public string Symbol { get; init; } = string.Empty;
    public string Condition { get; init; } = string.Empty;
    public decimal TargetPrice { get; init; }
    public bool IsEnabled { get; init; }
    public DateTimeOffset CreatedAt { get; init; }
}

public sealed record ServiceTogglesDto
{
    public bool News { get; init; }
    public bool Alerts { get; init; }
    public bool PortfolioSync { get; init; }
    public bool MarketOverview { get; init; }
}

public sealed record MobileAdminSummaryResponse
{
    public int UsersCount { get; init; }
    public int LinkedExchangesCount { get; init; }
    public int AlertsCount { get; init; }
    public int EnabledServicesCount { get; init; }
    public DateTimeOffset GeneratedAt { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record MobileAdminUserDto
{
    public Guid Id { get; init; }
    public string Email { get; init; } = string.Empty;
    public string Username { get; init; } = string.Empty;
    public string Status { get; init; } = string.Empty;
    public IReadOnlyList<string> Roles { get; init; } = [];
}

public sealed record MobileAdminServiceDto
{
    public string Name { get; init; } = string.Empty;
    public bool Enabled { get; init; }
    public string Status { get; init; } = string.Empty;
}

public sealed record MobileAdminStatisticsResponse
{
    public int UsersCount { get; init; }
    public int LinkedExchangesCount { get; init; }
    public int AlertsCount { get; init; }
    public int AvailableExchangesCount { get; init; }
    public DateTimeOffset GeneratedAt { get; init; } = DateTimeOffset.UtcNow;
}