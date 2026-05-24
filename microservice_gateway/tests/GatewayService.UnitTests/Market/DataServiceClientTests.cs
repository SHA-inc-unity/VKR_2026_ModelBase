using System.Text.Json;
using FluentAssertions;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace GatewayService.UnitTests.Market;

public sealed class DataServiceClientTests
{
    private static MarketSettings Settings() => new()
    {
        KafkaTimeoutSeconds = 2,
        IngestKafkaTimeoutSeconds = 5,
    };

    private static JsonElement Json(string json) => JsonDocument.Parse(json).RootElement.Clone();

  private static string SerializePayload(object payload) =>
    JsonSerializer.Serialize(payload, (JsonSerializerOptions?)null);

    [Fact]
    public async Task IngestAsync_uses_dataset_job_queue_and_waits_for_success()
    {
        var kafka = new Mock<IKafkaRequestClient>(MockBehavior.Strict);

        kafka.Setup(k => k.RequestAsync(
                DataTopics.CmdDataDatasetJobsStart,
                It.IsAny<object>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(Json("""
                {
                  "job_id": "11111111-1111-1111-1111-111111111111",
                  "status": "queued",
                  "deduped": false
                }
                """));

        kafka.Setup(k => k.RequestAsync(
                DataTopics.CmdDataDatasetJobsGet,
                It.IsAny<object>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(Json("""
                {
                  "job": {
                    "job_id": "11111111-1111-1111-1111-111111111111",
                    "status": "succeeded",
                    "target_table": "ethusdt_15",
                    "completed": 100
                  }
                }
                """));

        var sut = new DataServiceClient(
            kafka.Object,
            Options.Create(Settings()),
            NullLogger<DataServiceClient>.Instance);

        var result = await sut.IngestAsync("ETHUSDT", "15", 1_000, 2_000);

        result.Success.Should().BeTrue();
        result.TableName.Should().Be("ethusdt_15");
        result.RowsIngested.Should().Be(100);

        kafka.Verify(k => k.RequestAsync(
            DataTopics.CmdDataDatasetJobsStart,
            It.Is<object>(payload =>
            SerializePayload(payload).Contains("\"type\":\"ingest\"") &&
            SerializePayload(payload).Contains("\"target_table\":\"ethusdt_15\"") &&
            SerializePayload(payload).Contains("\"created_by\":\"gateway_market_chart\"") &&
            SerializePayload(payload).Contains("\"timeframe\":\"15\"")),
            It.IsAny<TimeSpan>(),
            It.IsAny<CancellationToken>()), Times.Once);

        kafka.Verify(k => k.RequestAsync(
            DataTopics.CmdDataDatasetJobsGet,
          It.Is<object>(payload => SerializePayload(payload).Contains("11111111-1111-1111-1111-111111111111")),
            It.IsAny<TimeSpan>(),
            It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task IngestAsync_reuses_existing_job_and_surfaces_terminal_failure()
    {
        var kafka = new Mock<IKafkaRequestClient>(MockBehavior.Strict);

        kafka.Setup(k => k.RequestAsync(
                DataTopics.CmdDataDatasetJobsStart,
                It.IsAny<object>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(Json("""
                {
                  "job_id": "22222222-2222-2222-2222-222222222222",
                  "status": "running",
                  "deduped": true
                }
                """));

        kafka.Setup(k => k.RequestAsync(
                DataTopics.CmdDataDatasetJobsGet,
                It.IsAny<object>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .ReturnsAsync(Json("""
                {
                  "job": {
                    "job_id": "22222222-2222-2222-2222-222222222222",
                    "status": "failed",
                    "target_table": "ethusdt_15",
                    "error_code": "bybit_rate_limited",
                    "error_message": "Bybit rate limit exceeded"
                  }
                }
                """));

        var sut = new DataServiceClient(
            kafka.Object,
            Options.Create(Settings()),
            NullLogger<DataServiceClient>.Instance);

        var result = await sut.IngestAsync("ETHUSDT", "15", 1_000, 2_000);

        result.Success.Should().BeFalse();
        result.TableName.Should().Be("ethusdt_15");
        result.Error.Should().Be("Bybit rate limit exceeded");
    }

}