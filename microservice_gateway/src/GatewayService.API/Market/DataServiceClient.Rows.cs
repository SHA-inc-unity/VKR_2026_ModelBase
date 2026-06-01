using System.Diagnostics;
using System.Text.Json;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

public sealed partial class DataServiceClient
{
    /// <inheritdoc />
    public async Task<RowsFetchResult> GetLatestWindowRowsAsync(
        string symbol,
        string bybitInterval,
        long stepMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        string exchange = "bybit",
        CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        var tableName = BuildTableName(symbol, bybitInterval, exchange);

        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetLatestRows,
                BuildRowsPayload(
                    new Dictionary<string, object?>
                    {
                        ["table"] = tableName,
                        ["step_ms"] = stepMs,
                        ["limit"] = limit,
                    },
                    columns),
                timeout,
                ct);

            if (TryGetReplyError(reply, out var replyError))
            {
                _log.LogWarning(
                    "data-service latest_rows error for {Table} stepMs={StepMs} limit={Limit}: code={Code} detail={Detail}",
                    tableName,
                    stepMs,
                    limit,
                    replyError.Code ?? "n/a",
                    replyError.Detail ?? "unknown error");
                return RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", BuildReplyErrorDetail(replyError));
            }

            if (reply.ValueKind == JsonValueKind.Object &&
                reply.TryGetProperty("claim_check", out _))
            {
                _log.LogWarning(
                    "data-service latest_rows returned a claim-check for {Table} stepMs={StepMs} limit={Limit}",
                    tableName, stepMs, limit);
                return RowsFetchResult.ClaimCheck;
            }

            return RowsFetchResult.From(ParseRows(reply));
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Latest window Kafka request timed out for {Table} stepMs={StepMs} limit={Limit}",
                tableName, stepMs, limit);
            return RowsFetchResult.Fail(
                "DOWNSTREAM_TIMEOUT",
                $"latest_rows timed out for {tableName} stepMs={stepMs} limit={limit}");
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Latest window Kafka request failed for {Table} stepMs={StepMs} limit={Limit}",
                tableName, stepMs, limit);
            return RowsFetchResult.Fail(
                "DATA_SOURCE_UNAVAILABLE",
                $"latest_rows failed for {tableName} stepMs={stepMs} limit={limit}: {ex.Message}");
        }
    }

    /// <inheritdoc />
    public async Task<RowsFetchResult> GetRowsAsync(
        string tableName,
        long startMs,
        long endMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetRows,
                BuildRowsPayload(
                    new Dictionary<string, object?>
                    {
                        ["table"] = tableName,
                        ["start_ms"] = startMs,
                        ["end_ms"] = endMs,
                        ["limit"] = limit,
                    },
                    columns),
                timeout,
                ct);

            if (TryGetReplyError(reply, out var replyError))
            {
                _log.LogWarning(
                    "data-service rows error for {Table} [{Start}..{End}] limit={Limit}: code={Code} detail={Detail}",
                    tableName,
                    startMs,
                    endMs,
                    limit,
                    replyError.Code ?? "n/a",
                    replyError.Detail ?? "unknown error");
                return RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", BuildReplyErrorDetail(replyError));
            }

            if (reply.ValueKind == JsonValueKind.Object &&
                reply.TryGetProperty("claim_check", out _))
            {
                // Payload exceeded Kafka message limit even with the limit parameter.
                // Returning a typed ClaimCheck so ChartService can handle this case
                // without triggering a spurious new ingest (the data IS there).
                _log.LogWarning(
                    "data-service rows returned a claim-check for {Table} [{Start}..{End}] " +
                    "limit={Limit} — payload too large; client should reduce limit",
                    tableName, startMs, endMs, limit);
                return RowsFetchResult.ClaimCheck;
            }

            return RowsFetchResult.From(ParseRows(reply));
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Rows Kafka request timed out for {Table} [{Start}..{End}] limit={Limit}",
                tableName, startMs, endMs, limit);
            return RowsFetchResult.Fail(
                "DOWNSTREAM_TIMEOUT",
                $"rows timed out for {tableName} [{startMs}..{endMs}] limit={limit}");
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Rows Kafka request failed for {Table} [{Start}..{End}] limit={Limit}",
                tableName, startMs, endMs, limit);
            return RowsFetchResult.Fail(
                "DATA_SOURCE_UNAVAILABLE",
                $"rows failed for {tableName} [{startMs}..{endMs}] limit={limit}: {ex.Message}");
        }
    }
}
