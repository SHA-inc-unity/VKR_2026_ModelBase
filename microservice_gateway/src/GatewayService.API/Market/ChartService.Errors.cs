using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

public partial class ChartService
{
    private ServiceResult<ChartResponse> BuildRowsFailureResult(
        string symbol,
        string timeframe,
        int limit,
        RowsFetchResult rowsResult,
        string operation)
    {
        return ServiceResult<ChartResponse>.Fail(
            BuildRowsFailureError(symbol, timeframe, limit, rowsResult, operation));
    }

    private string BuildRowsFailureError(
        string symbol,
        string timeframe,
        int limit,
        RowsFetchResult rowsResult,
        string operation)
    {
        var errorCode = string.IsNullOrWhiteSpace(rowsResult.ErrorCode)
            ? "DATA_SOURCE_UNAVAILABLE"
            : rowsResult.ErrorCode;
        var errorDetail = string.IsNullOrWhiteSpace(rowsResult.ErrorDetail)
            ? $"data-service {operation} failed for {symbol}/{timeframe} limit={limit}"
            : rowsResult.ErrorDetail;

        _log.LogWarning(
            "Chart request failed for {Symbol}/{Timeframe} limit={Limit}: {Code} {Detail}",
            symbol,
            timeframe,
            limit,
            errorCode,
            errorDetail);

        return $"{errorCode}: {errorDetail}";
    }

    private string BuildIngestFailureError(
        string symbol,
        string timeframe,
        int limit,
        IngestResult ingestResult)
    {
        var errorCode = string.IsNullOrWhiteSpace(ingestResult.ErrorCode)
            ? "SERVICE_BUSY"
            : ingestResult.ErrorCode;
        var errorDetail = string.IsNullOrWhiteSpace(ingestResult.ErrorDetail)
            ? ingestResult.Error ?? $"chart hydration failed for {symbol}/{timeframe} limit={limit}"
            : ingestResult.ErrorDetail;

        _log.LogWarning(
            "Chart hydration failed for {Symbol}/{Timeframe} limit={Limit}: {Code} {Detail}",
            symbol,
            timeframe,
            limit,
            errorCode,
            errorDetail);

        return $"{errorCode}: {errorDetail}";
    }

    private static string BuildHydrationPendingReason(IngestResult ingestResult)
    {
        return string.IsNullOrWhiteSpace(ingestResult.ErrorDetail)
            ? "Chart hydration is still in progress"
            : ingestResult.ErrorDetail!;
    }

    // Data-service tables use the canonical client timeframe key
    // ("60m", "1d"), not the Bybit kline interval ("60", "D"). Sending
    // the Bybit value here would point us at non-existent tables and
    // surface as 42P01 in data-service logs / 503 rows-timeout to clients.
    // For non-Bybit exchanges DatasetCore prefixes the exchange ("binance_btcusdt_60m").
    private static string BuildTableName(string symbol, string bybitInterval, string exchange)
    {
        return DataServiceClient.BuildTableName(symbol, bybitInterval, exchange);
    }
}
