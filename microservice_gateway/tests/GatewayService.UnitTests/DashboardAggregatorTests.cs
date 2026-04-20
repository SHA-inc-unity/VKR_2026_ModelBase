using FluentAssertions;
using GatewayService.API.Aggregators.Dashboard;
using Xunit;
using GatewayService.API.Clients.Market;
using GatewayService.API.Clients.News;
using GatewayService.API.Clients.Portfolio;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;

namespace GatewayService.UnitTests;

public sealed class DashboardAggregatorTests
{
    private readonly Mock<IPortfolioServiceClient> _portfolioMock = new();
    private readonly Mock<IMarketServiceClient> _marketMock = new();
    private readonly Mock<INewsServiceClient> _newsMock = new();

    private DashboardAggregator CreateSut() =>
        new(_portfolioMock.Object, _marketMock.Object, _newsMock.Object,
            NullLogger<DashboardAggregator>.Instance);

    private void SetAllFail()
    {
        _portfolioMock.Setup(c => c.GetSummaryAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServiceResult<PortfolioSummaryDto>.Fail("not available"));
        _marketMock.Setup(c => c.GetOverviewAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServiceResult<MarketOverviewDto>.Fail("not available"));
        _marketMock.Setup(c => c.GetTrendingAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServiceResult<IReadOnlyList<TrendingAssetDto>>.Fail("not available"));
        _newsMock.Setup(c => c.GetLatestAsync(It.IsAny<int>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(ServiceResult<IReadOnlyList<NewsItemDto>>.Fail("not available"));
    }

    [Fact]
    public async Task All_services_fail_produces_degraded_sections()
    {
        SetAllFail();
        var sut = CreateSut();

        var result = await sut.AggregateAsync("user-1");

        result.Portfolio.Should().BeNull();
        result.MarketOverview.Should().BeNull();
        result.TrendingAssets.Should().BeEmpty();
        result.LatestNews.Should().BeEmpty();
        result.Meta.DegradedSections.Should().Contain(["portfolio", "market", "news"]);
    }

    [Fact]
    public async Task Portfolio_success_populates_section()
    {
        var summary = new PortfolioSummaryDto { TotalValueUsd = 1000m, PnlPercent24h = 1.5m, AssetCount = 3 };
        _portfolioMock.Setup(c => c.GetSummaryAsync("u", default))
            .ReturnsAsync(ServiceResult<PortfolioSummaryDto>.Ok(summary));
        _marketMock.Setup(c => c.GetOverviewAsync(default))
            .ReturnsAsync(ServiceResult<MarketOverviewDto>.Fail("not available"));
        _marketMock.Setup(c => c.GetTrendingAsync(It.IsAny<int>(), default))
            .ReturnsAsync(ServiceResult<IReadOnlyList<TrendingAssetDto>>.Fail("not available"));
        _newsMock.Setup(c => c.GetLatestAsync(It.IsAny<int>(), default))
            .ReturnsAsync(ServiceResult<IReadOnlyList<NewsItemDto>>.Fail("not available"));

        var sut = CreateSut();
        var result = await sut.AggregateAsync("u");

        result.Portfolio.Should().NotBeNull();
        result.Portfolio!.TotalValueUsd.Should().Be(1000m);
        result.Meta.DegradedSections.Should().NotContain("portfolio");
        result.Meta.DegradedSections.Should().Contain("market");
    }
}
