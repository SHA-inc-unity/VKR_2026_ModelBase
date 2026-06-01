using System.Diagnostics;
using System.Text.Json;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

public sealed partial class DataServiceClient
{
    /// <inheritdoc />
    public async Task<CoverageResult?> GetCoverageAsync(
        string symbol, string bybitInterval, string exchange = "bybit", CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        // Data-service normalises the timeframe via DatasetCore.NormalizeTimeframe
        // which expects the canonical client key ("60m"), not Bybit's "60".
        var timeframeKey = TimeframeMap.BybitIntervalToClientId(bybitInterval);
        var exchangeKey = NormalizeExchange(exchange);
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetCoverage,
                new { symbol, timeframe = timeframeKey, exchange = exchangeKey },
                timeout,
                ct);

            return ParseCoverage(reply);
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex, "Coverage Kafka request timed out for {Symbol}/{Interval}@{Exchange}",
                symbol, bybitInterval, exchangeKey);
            return null;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Coverage Kafka request failed for {Symbol}/{Interval}@{Exchange}",
                symbol, bybitInterval, exchangeKey);
            return null;
        }
    }
}
