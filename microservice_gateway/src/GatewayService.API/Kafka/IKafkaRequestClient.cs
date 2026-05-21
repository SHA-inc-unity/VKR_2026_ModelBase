using System.Text.Json;

namespace GatewayService.API.Kafka;

/// <summary>
/// Thin abstraction over the gateway Kafka request/reply client.
/// Lets downstream services unit-test their request sequencing without
/// bootstrapping a live KafkaRequestClient hosted loop.
/// </summary>
public interface IKafkaRequestClient
{
    Task<JsonElement> RequestAsync(
        string topic,
        object payload,
        TimeSpan timeout,
        CancellationToken ct = default);
}

/// <summary>
/// Read-only probe for the live Kafka request/reply state inside gateway.
/// Used by readiness checks and diagnostics without exposing send semantics.
/// </summary>
public interface IKafkaRequestClientProbe
{
    bool IsReplyInboxReady { get; }
    string ReplyInbox { get; }
    string ReplyInboxStatus { get; }
}