using FluentAssertions;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Xunit;

namespace GatewayService.UnitTests;

public sealed class KafkaRequestReplyHealthCheckTests
{
    [Fact]
    public async Task CheckHealthAsync_returns_healthy_when_reply_inbox_is_ready()
    {
        var probe = new ProbeStub(
            IsReplyInboxReady: true,
            ReplyInbox: "reply.gateway.test",
            ReplyInboxStatus: "assigned to partitions: reply.gateway.test [0]");
        var sut = new KafkaRequestReplyHealthCheck(probe);

        var result = await sut.CheckHealthAsync(new HealthCheckContext());

        result.Status.Should().Be(HealthStatus.Healthy);
        result.Description.Should().Contain("reply.gateway.test");
        result.Description.Should().Contain("assigned to partitions");
    }

    [Fact]
    public async Task CheckHealthAsync_returns_unhealthy_when_reply_inbox_is_not_ready()
    {
        var probe = new ProbeStub(
            IsReplyInboxReady: false,
            ReplyInbox: "reply.gateway.test",
            ReplyInboxStatus: "reply inbox topic create exceeded startup budget; trying bootstrap produce");
        var sut = new KafkaRequestReplyHealthCheck(probe);

        var result = await sut.CheckHealthAsync(new HealthCheckContext());

        result.Status.Should().Be(HealthStatus.Unhealthy);
        result.Description.Should().Contain("not assigned yet");
        result.Description.Should().Contain("startup budget");
    }

    private sealed record ProbeStub(
        bool IsReplyInboxReady,
        string ReplyInbox,
        string ReplyInboxStatus) : IKafkaRequestClientProbe;
}