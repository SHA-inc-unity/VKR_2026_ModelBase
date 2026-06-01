using System.Diagnostics;
using System.Text.Json;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed partial class DataServiceClient : IDataServiceClient
{
    // Data-service supports a server-side long-poll on cmd.data.dataset.jobs.get
    // via the wait_terminal_ms payload field — see HandleJobsGetAsync.
    // We use a 1500 ms server-side wait + 200 ms client-side back-off, which
    // means a typical chart-ingest run (~300-800 ms with skip_features) finishes
    // in a single Kafka roundtrip instead of the previous ~10-20 roundtrips
    // (50 ms client poll). Falls back to short-poll cleanly if the data-service
    // is older and ignores the field.
    private static readonly TimeSpan IngestJobPollDelay = TimeSpan.FromMilliseconds(200);
    private const int JobsGetServerWaitMs = 1500;

    private readonly IKafkaRequestClient _kafka;
    private readonly MarketSettings      _settings;
    private readonly ILogger<DataServiceClient> _log;

    public DataServiceClient(
        IKafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<DataServiceClient> log)
    {
        _kafka    = kafka;
        _settings = settings.Value;
        _log      = log;
    }

    // ── Parsers ───────────────────────────────────────────────────────────

    // Data-service stores OHLCV in tables whose name depends on the exchange:
    //   bybit (default) → "{symbol}_{tfKey}"            (e.g. btcusdt_60m)
    //   other exchanges → "{exchange}_{symbol}_{tfKey}" (e.g. binance_btcusdt_60m)
    // tfKey is the canonical client id ("60m", "1d"), NOT the Bybit kline
    // interval ("60", "D"). This mirrors DatasetCore.MakeTableName in data-service.
    public static string BuildTableName(string symbol, string bybitInterval, string exchange = "bybit")
    {
        var tfKey = TimeframeMap.BybitIntervalToClientId(bybitInterval);
        var sym = symbol.ToLowerInvariant();
        var ex = NormalizeExchange(exchange);
        return ex == "bybit"
            ? $"{sym}_{tfKey}"
            : $"{ex}_{sym}_{tfKey}";
    }

    /// <summary>
    /// Normalises and validates an exchange identifier.
    /// Unknown/blank values fall back to "bybit" (current default), preserving
    /// the legacy code path for all callers that don't supply an exchange.
    /// </summary>
    public static string NormalizeExchange(string? exchange)
    {
        if (string.IsNullOrWhiteSpace(exchange))
            return "bybit";

        var trimmed = exchange.Trim().ToLowerInvariant();
        return trimmed switch
        {
            "bybit" or "binance" => trimmed,
            _ => "bybit",
        };
    }

    private static bool TryGetNestedJob(JsonElement el, out JsonElement job)
    {
        if (el.ValueKind == JsonValueKind.Object &&
            el.TryGetProperty("job", out job) &&
            job.ValueKind == JsonValueKind.Object)
        {
            return true;
        }

        job = default;
        return false;
    }

    private static bool TryGetError(JsonElement el, out string error)
    {
        error = string.Empty;
        if (el.ValueKind != JsonValueKind.Object ||
            !el.TryGetProperty("error", out var errEl))
            return false;

        var detail = errEl.ValueKind switch
        {
            JsonValueKind.String => errEl.GetString(),
            _ => errEl.ToString(),
        };
        var code = TryGetString(el, "code");
        error = string.IsNullOrWhiteSpace(code)
            ? detail ?? "unknown error"
            : $"{code}: {detail}";
        return true;
    }

    private static bool TryGetReplyError(JsonElement el, out ReplyError error)
    {
        error = default;
        if (el.ValueKind != JsonValueKind.Object ||
            !el.TryGetProperty("error", out var errEl))
        {
            return false;
        }

        error = new ReplyError(
            TryGetString(el, "code"),
            errEl.ValueKind switch
            {
                JsonValueKind.String => errEl.GetString(),
                _ => errEl.ToString(),
            });
        return true;
    }

    private static string BuildReplyErrorDetail(ReplyError error)
    {
        if (string.IsNullOrWhiteSpace(error.Code))
            return error.Detail ?? "unknown error";

        if (string.IsNullOrWhiteSpace(error.Detail))
            return error.Code!;

        return $"{error.Code}: {error.Detail}";
    }

    private static string NormalizeIngestErrorCode(string? errorCode, string fallbackCode)
    {
        if (string.IsNullOrWhiteSpace(errorCode))
            return fallbackCode;

        return errorCode.Trim().ToUpperInvariant() switch
        {
            "DOWNSTREAM_TIMEOUT" => "DOWNSTREAM_TIMEOUT",
            "DATA_SOURCE_UNAVAILABLE" => "DATA_SOURCE_UNAVAILABLE",
            "SERVICE_BUSY" => "SERVICE_BUSY",
            _ => fallbackCode,
        };
    }

    private static string BuildJobFailure(JsonElement job, string fallbackStatus)
    {
        var errorCode = TryGetString(job, "error_code");
        var errorMessage = TryGetString(job, "error_message");
        if (!string.IsNullOrWhiteSpace(errorMessage))
            return errorMessage!;
        if (!string.IsNullOrWhiteSpace(errorCode))
            return errorCode!;
        return $"job_{fallbackStatus}";
    }

    private static string? TryGetString(JsonElement el, string name)
    {
        if (!el.TryGetProperty(name, out var value)) return null;
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Number => value.ToString(),
            JsonValueKind.True => bool.TrueString,
            JsonValueKind.False => bool.FalseString,
            _ => null,
        };
    }

    private static int ClampToInt(long value)
    {
        if (value <= 0) return 0;
        if (value >= int.MaxValue) return int.MaxValue;
        return (int)value;
    }

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

    private readonly record struct ReplyError(string? Code, string? Detail);

    /// <summary>
    /// Columns the chart path actually consumes from data-service rows replies.
    /// Used to ask the data-service to project only these columns instead of
    /// returning the full feature-engineered row (40+ columns), which was the
    /// dominant cost behind the Kafka rows timeout on cold tables.
    /// </summary>
    public static readonly IReadOnlyList<string> ChartProjectionColumns = new[]
    {
        "timestamp_utc",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
        "turnover",
    };

    private static Dictionary<string, object?> BuildRowsPayload(
        Dictionary<string, object?> basePayload,
        IReadOnlyList<string>? columns)
    {
        if (columns is { Count: > 0 })
            basePayload["columns"] = columns;
        return basePayload;
    }
}
