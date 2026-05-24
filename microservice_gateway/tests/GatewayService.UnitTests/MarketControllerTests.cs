using FluentAssertions;
using GatewayService.API.Clients.Market;
using GatewayService.API.Common;
using GatewayService.API.Controllers;
using GatewayService.API.DTOs;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using GatewayService.API.Middleware;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Moq;
using Xunit;

namespace GatewayService.UnitTests;

public sealed class MarketControllerTests
{
    [Fact]
    public async Task GetChart_returns_503_for_downstream_chart_errors()
    {
        var controller = CreateController(
            ServiceResult<ChartResponse>.Fail(
                "DATA_SOURCE_UNAVAILABLE: data-service latest_rows failed"));

        var result = await controller.GetChart("BTCUSDT", "5m", 200, CancellationToken.None);

        var objectResult = result.Should().BeOfType<ObjectResult>().Subject;
        objectResult.StatusCode.Should().Be(StatusCodes.Status503ServiceUnavailable);
        objectResult.Value.Should().BeEquivalentTo(new ErrorResponse
        {
            Status = 503,
            Title = "Service Unavailable",
            Code = "DATA_SOURCE_UNAVAILABLE",
            Detail = "data-service latest_rows failed",
            CorrelationId = "corr-chart",
        }, options => options.Excluding(item => item.Timestamp));
    }

    [Fact]
    public async Task GetChart_returns_400_for_validation_errors()
    {
        var controller = CreateController(
            ServiceResult<ChartResponse>.Fail(
                "INVALID_LIMIT: 150 is not in the allowed candle count grid"));

        var result = await controller.GetChart("BTCUSDT", "5m", 150, CancellationToken.None);

        var objectResult = result.Should().BeOfType<BadRequestObjectResult>().Subject;
        objectResult.StatusCode.Should().Be(StatusCodes.Status400BadRequest);
        objectResult.Value.Should().BeEquivalentTo(new ErrorResponse
        {
            Status = 400,
            Title = "Bad Request",
            Detail = "INVALID_LIMIT: 150 is not in the allowed candle count grid",
            CorrelationId = "corr-chart",
        }, options => options.Excluding(item => item.Timestamp));
    }

    private static MarketController CreateController(ServiceResult<ChartResponse> chartResult)
    {
        var controller = new MarketController(
            Mock.Of<IMarketServiceClient>(),
            Mock.Of<IMarketConfigService>(),
            new StubChartService(chartResult))
        {
            ControllerContext = new ControllerContext
            {
                HttpContext = new DefaultHttpContext()
            }
        };

        controller.ControllerContext.HttpContext.Items[CorrelationIdMiddleware.ItemsKey] = "corr-chart";
        return controller;
    }

    private sealed class StubChartService : IChartService
    {
        private readonly ServiceResult<ChartResponse> _result;

        public StubChartService(ServiceResult<ChartResponse> result)
        {
            _result = result;
        }

        public Task<ServiceResult<ChartResponse>> GetChartAsync(
            string symbol,
            string timeframe,
            int limit,
            CancellationToken ct = default)
        {
            return Task.FromResult(_result);
        }
    }
}