using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

/// <summary>
/// Validates chart requests, coordinates data-service calls, tries a bounded
/// synchronous hydrate when chart windows are missing or incomplete, and
/// applies cache policy.
/// </summary>
public interface IChartService
{
    /// <summary>
    /// Returns a chart response for the given parameters.
    ///
    /// All validation errors are encoded as
    /// <see cref="ServiceResult{T}.IsFailure"/> so the controller can map
    /// them to the correct HTTP status code without exception handling.
    ///
    /// Possible failure reasons:
    /// - "INVALID_SYMBOL"    → symbol not in the known-symbol list → 400
    /// - "INVALID_TIMEFRAME" → timeframe id not in TimeframeMap    → 400
    /// - "INVALID_LIMIT"     → count not in CandleCountGrid        → 400
    /// - "DATA_SOURCE_UNAVAILABLE" → data-service replied with an error → 503
    /// - "DOWNSTREAM_TIMEOUT"      → data-service timed out             → 503
    /// - "SERVICE_BUSY"            → hydrate path failed/cooldown       → 503
    /// </summary>
    Task<ServiceResult<ChartResponse>> GetChartAsync(
        string symbol, string timeframe, int limit,
        string exchange = "bybit", CancellationToken ct = default);

    /// <summary>
    /// Returns the page of <paramref name="limit"/> candles immediately OLDER
    /// than the <paramref name="beforeMs"/> cursor (exclusive) — used for
    /// infinite left-panning. Backfills the requested historical window from
    /// the exchange on demand. An empty page signals the start of available
    /// history; failures use the same reason codes as <see cref="GetChartAsync"/>
    /// plus "INVALID_CURSOR" for a non-positive cursor.
    /// </summary>
    Task<ServiceResult<ChartResponse>> GetChartBeforeAsync(
        string symbol, string timeframe, int limit, long beforeMs,
        string exchange = "bybit", CancellationToken ct = default);
}
