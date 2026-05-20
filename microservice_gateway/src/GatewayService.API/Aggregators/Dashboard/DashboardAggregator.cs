using GatewayService.API.Common;
using GatewayService.API.Clients.Market;
using GatewayService.API.Clients.News;
using GatewayService.API.Clients.Portfolio;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Aggregators.Dashboard;

public sealed class DashboardAggregator : IDashboardAggregator
{
    private readonly IPortfolioServiceClient _portfolio;
    private readonly IMarketServiceClient _market;
    private readonly INewsServiceClient _news;
    private readonly ILogger<DashboardAggregator> _logger;

    public DashboardAggregator(
        IPortfolioServiceClient portfolio,
        IMarketServiceClient market,
        INewsServiceClient news,
        ILogger<DashboardAggregator> logger)
    {
        _portfolio = portfolio;
        _market = market;
        _news = news;
        _logger = logger;
    }

    public async Task<DashboardResponse> AggregateAsync(string? userId, CancellationToken ct = default)
    {
        // Fire all downstream calls in parallel — no single service failure kills the whole response.
        // Guests do not have a personal portfolio section, so we do not call that downstream at all.
        Task<ServiceResult<PortfolioSummaryDto>>? portfolioTask = null;
        if (!string.IsNullOrWhiteSpace(userId))
            portfolioTask = _portfolio.GetSummaryAsync(userId, ct);

        var marketTask = _market.GetOverviewAsync(ct);
        var trendingTask = _market.GetTrendingAsync(limit: 5, ct);
        var newsTask = _news.GetLatestAsync(limit: 5, ct);

        var tasks = new List<Task> { marketTask, trendingTask, newsTask };
        if (portfolioTask is not null) tasks.Add(portfolioTask);

        await Task.WhenAll(tasks);

        var degraded = new List<string>();

        PortfolioSummaryDto? portfolio = null;
        if (portfolioTask is not null)
        {
            var portfolioResult = await portfolioTask;
            if (portfolioResult.IsSuccess) portfolio = portfolioResult.Value;
            else { degraded.Add("portfolio"); Log("portfolio", portfolioResult.Error); }
        }

        MarketOverviewDto? marketOverview = null;
        var marketResult = await marketTask;
        if (marketResult.IsSuccess) marketOverview = marketResult.Value;
        else { degraded.Add("market"); Log("market", marketResult.Error); }

        IReadOnlyList<TrendingAssetDto> trending = [];
        var trendingResult = await trendingTask;
        if (trendingResult.IsSuccess) trending = trendingResult.Value ?? [];
        // trending doesn't mark the whole market as degraded if overview also failed (already added)

        IReadOnlyList<NewsTeaserDto> newsTeasers = [];
        var newsResult = await newsTask;
        if (newsResult.IsSuccess && newsResult.Value is { } newsItems)
        {
            newsTeasers = newsItems
                .Take(5)
                .Select(n => new NewsTeaserDto
                {
                    Title = n.Title,
                    Source = n.Source,
                    PublishedAt = n.PublishedAt,
                    ImageUrl = n.ImageUrl
                })
                .ToList();
        }
        else { degraded.Add("news"); Log("news", newsResult.Error); }

        return new DashboardResponse
        {
            Portfolio = portfolio,
            MarketOverview = marketOverview,
            TrendingAssets = trending,
            LatestNews = newsTeasers,
            Meta = new DashboardMetaDto
            {
                DegradedSections = degraded,
                GeneratedAt = DateTimeOffset.UtcNow
            }
        };
    }

    private void Log(string service, string? error) =>
        _logger.LogWarning("{Service} service degraded during dashboard aggregation: {Error}", service, error);
}
