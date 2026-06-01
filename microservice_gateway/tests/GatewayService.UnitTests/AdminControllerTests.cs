using FluentAssertions;
using GatewayService.API.Controllers;
using GatewayService.API.DTOs;
using GatewayService.API.Kafka;
using GatewayService.API.Middleware;
using GatewayService.API.Settings;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace GatewayService.UnitTests;

public sealed class AdminControllerTests
{
    [Fact]
    public async Task DatasetListTables_returns_503_when_kafka_publish_fails()
    {
        var kafka = new Mock<IKafkaRequestClient>(MockBehavior.Strict);
        kafka.Setup(x => x.RequestAsync(
                AdminTopics.DatasetListTables,
                It.IsAny<object>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .ThrowsAsync(new TaskCanceledException("A task was canceled."));

        var controller = CreateController(kafka.Object, "/api/admin/dataset/list-tables", "corr-kafka-down");

        var result = await controller.DatasetListTables(body: null, CancellationToken.None);

        var objectResult = result.Should().BeOfType<ObjectResult>().Subject;
        objectResult.StatusCode.Should().Be(StatusCodes.Status503ServiceUnavailable);
        objectResult.Value.Should().BeEquivalentTo(new ErrorResponse
        {
            Status = 503,
            Title = "Admin Facade Upstream Unavailable",
            Code = "admin_kafka_unavailable",
            Detail = "Gateway could not publish the Kafka request. Check Redpanda/Kafka broker connectivity and the bootstrap listener.",
            CorrelationId = "corr-kafka-down",
        }, options => options.Excluding(x => x.Timestamp));

        kafka.VerifyAll();
    }

    [Fact]
    public async Task DatasetListTables_returns_504_when_kafka_reply_times_out()
    {
        var kafka = new Mock<IKafkaRequestClient>(MockBehavior.Strict);
        kafka.Setup(x => x.RequestAsync(
                AdminTopics.DatasetListTables,
                It.IsAny<object>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .ThrowsAsync(new TimeoutException("Kafka request timed out on cmd.data.dataset.list_tables"));

        var controller = CreateController(kafka.Object, "/api/admin/dataset/list-tables", "corr-timeout");

        var result = await controller.DatasetListTables(body: null, CancellationToken.None);

        var objectResult = result.Should().BeOfType<ObjectResult>().Subject;
        objectResult.StatusCode.Should().Be(StatusCodes.Status504GatewayTimeout);
        objectResult.Value.Should().BeEquivalentTo(new ErrorResponse
        {
            Status = 504,
            Title = "Admin Facade Timeout",
            Code = "admin_kafka_timeout",
            Detail = "Kafka request timed out on cmd.data.dataset.list_tables",
            CorrelationId = "corr-timeout",
        }, options => options.Excluding(x => x.Timestamp));

        kafka.VerifyAll();
    }

    private static AdminController CreateController(
        IKafkaRequestClient kafka,
        string path,
        string correlationId)
    {
        var controller = new AdminController(
            kafka,
            Mock.Of<IAdminEventRelayHub>(),
            Options.Create(new AdminSettings()),
            NullLogger<AdminController>.Instance);

        var httpContext = new DefaultHttpContext();
        httpContext.Request.Path = path;
        httpContext.Items[CorrelationIdMiddleware.ItemsKey] = correlationId;

        controller.ControllerContext = new ControllerContext
        {
            HttpContext = httpContext,
        };

        return controller;
    }
}