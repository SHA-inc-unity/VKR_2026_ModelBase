using System.Text.Json;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed class DataServiceClient : IDataServiceClient
{
    private readonly KafkaRequestClient _kafka;
    private readonly MarketSettings     _settings;
    private readonly ILogger<DataServiceClient> _log;

    public DataServiceClient(
        KafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<DataServiceClient> log)
    {
        _kafka    = kafka;
        _settings = settings.Value;
        _log      = log;
    }

    /// <inheritdoc />
    public async Task<CoverageResult?> GetCoverageAsync(
        string symbol, string bybitInterval, CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetCoverage,
                new { symbol, timeframe = bybitInterval },
                timeout,
                ct);

            return ParseCoverage(reply);
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex, "Coverage Kafka request timed out for {Symbol}/{Interval}",
                symbol, bybitInterval);
            return null;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Coverage Kafka request failed for {Symbol}/{Interval}",
                symbol, bybitInterval);
            return null;
        }
    }

    /// <inheritdoc />
    public async Task<RowsResult> GetRowsAsync(
        string tableName, long startMs, long endMs, int limit, CancellationToken ct = default)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataDatasetRows,
                new { table = tableName, start_ms = startMs, end_ms = endMs, limit },
                timeout,
                ct);

            if (reply.ValueKind == JsonValueKind.Object &&
                reply.TryGetProperty("error", out var errEl))
            {
                _log.LogWarning(
                    "data-service rows error for {Table} [{Start}..{End}] limit={Limit}: {Error}",
                    tableName, startMs, endMs, limit, errEl.GetString());
                return RowsResult.Empty;
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
                return RowsResult.ClaimCheck;
            }

            return RowsResult.From(ParseRows(reply));
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex,
                "Rows Kafka request timed out for {Table} [{Start}..{End}] limit={Limit}",
                tableName, startMs, endMs, limit);
            return RowsResult.Empty;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Rows Kafka request failed for {Table} [{Start}..{End}] limit={Limit}",
                tableName, startMs, endMs, limit);
            return RowsResult.Empty;
        }
    }

    /// <inheritdoc />
    public void FireAndForgetIngest(
        string symbol, string bybitInterval, long startMs, long endMs,
        Action onComplete, Action<Exception> onError)
    {
        var timeout = TimeSpan.FromSeconds(_settings.IngestKafkaTimeoutSeconds);
        _ = Task.Run(async () =>
        {
            try
            {
                var reply = await _kafka.RequestAsync(
                    DataTopics.CmdDataDatasetIngest,
                    new
                    {
                        symbol,
                        timeframe = bybitInterval,
                        start_ms  = startMs,
                        end_ms    = endMs,
                    },
                    timeout,
                    CancellationToken.None);

                if (reply.ValueKind == JsonValueKind.Object &&
                    reply.TryGetProperty("error", out var errEl))
                {
                    var msg = errEl.GetString() ?? "unknown error";
                    _log.LogError(
                        "Ingest failed (data-service error) for {Symbol}/{Interval}: {Error}",
                        symbol, bybitInterval, msg);
                    onError(new InvalidOperationException(msg));
                }
                else
                {
                    var rowsIngested = reply.ValueKind == JsonValueKind.Object &&
                        reply.TryGetProperty("rows_ingested", out var ri) &&
                        ri.ValueKind == JsonValueKind.Number
                        ? ri.GetInt32()
                        : -1;
                    _log.LogInformation(
                        "Ingest completed for {Symbol}/{Interval}: {Rows} rows ingested",
                        symbol, bybitInterval, rowsIngested);
                    onComplete();
                }
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Background ingest task failed for {Symbol}/{Interval}",
                    symbol, bybitInterval);
                onError(ex);
            }
        });
    }

    // ── Parsers ───────────────────────────────────────────────────────────

    private static CoverageResult? ParseCoverage(JsonElement el)
    {
        if (el.ValueKind != JsonValueKind.Object)
            return null;

        if (el.TryGetProperty("error", out _))
            return null;

        var exists = el.TryGetProperty("exists", out var existsEl) &&
                     existsEl.ValueKind == JsonValueKind.True;

        var tableName = el.TryGetProperty("table_name", out var tnEl)
            ? tnEl.GetString() ?? string.Empty
            : string.Empty;

        if (!exists)
            return new CoverageResult(false, tableName, 0, 0, 0, 0.0);

        var rows    = GetLong(el, "rows");
        var minTs   = GetLong(el, "min_ts_ms");
        var maxTs   = GetLong(el, "max_ts_ms");
        var covPct  = el.TryGetProperty("coverage_pct", out var cpEl) &&
                      cpEl.ValueKind == JsonValueKind.Number
                      ? cpEl.GetDouble()
                      : 0.0;

        return new CoverageResult(true, tableName, rows, minTs, maxTs, covPct);
    }

    private IReadOnlyList<CandleRow> ParseRows(JsonElement el)
    {
        if (el.ValueKind != JsonValueKind.Object ||
            !el.TryGetProperty("rows", out var rowsEl) ||
            rowsEl.ValueKind != JsonValueKind.Array)
            return [];

        var result = new List<CandleRow>();
        foreach (var row in rowsEl.EnumerateArray())
        {
            if (row.ValueKind != JsonValueKind.Object)
                continue;

            var tsMs     = GetLong(row, "timestamp_ms");
            var open     = GetDecimal(row, "open_price");
            var high     = GetDecimal(row, "high_price");
            var low      = GetDecimal(row, "low_price");
            var close    = GetDecimal(row, "close_price");
            var volume   = GetDecimal(row, "volume");
            var turnover = GetDecimal(row, "turnover");

            // Skip rows with invalid OHLC values
            if (tsMs == 0 || open == 0 || close == 0)
                continue;

            result.Add(new CandleRow(tsMs, open, high, low, close, volume, turnover));
        }

        return result;
    }

    private static long GetLong(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var v)) return 0;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n)) return n;
        if (v.ValueKind == JsonValueKind.String &&
            long.TryParse(v.GetString(), out var s)) return s;
        return 0;
    }

    private static decimal GetDecimal(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var v)) return 0;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetDecimal(out var d)) return d;
        if (v.ValueKind == JsonValueKind.String &&
            decimal.TryParse(v.GetString(),
                System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture,
                out var s))
            return s;
        return 0;
    }
}
