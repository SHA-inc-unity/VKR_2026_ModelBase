using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

/// <summary>
/// Validates chart requests, coordinates data-service calls, triggers
/// background ingests when data is missing, and applies cache policy.
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
    /// - "SERVICE_BUSY"            → hydrate path cannot be started     → 503
    /// </summary>
    Task<ServiceResult<ChartResponse>> GetChartAsync(
        string symbol, string timeframe, int limit, CancellationToken ct = default);
}
