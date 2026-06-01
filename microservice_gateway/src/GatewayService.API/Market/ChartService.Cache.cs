using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Market;

public partial class ChartService
{
    public async Task<ChartResponse?> TryGetCachedChartAsync(
        string symbol,
        string timeframe,
        int limit,
        string exchange = "bybit",
        CancellationToken ct = default)
    {
        if (!TimeframeMap.TryGetById(timeframe, out var tfInfo))
            return null;

        return await TryGetCachedChartAsync(
            symbol.ToUpperInvariant(),
            tfInfo,
            limit,
            DataServiceClient.NormalizeExchange(exchange),
            ct);
    }

    public async Task<ChartResponse?> TryGetCachedChartAsync(
        string symbol,
        TimeframeInfo tfInfo,
        int limit,
        string exchange,
        CancellationToken ct = default)
    {
        var exactKey = BuildCacheKey(symbol, tfInfo, limit, exchange);
        var exact = await _cache.GetAsync<ChartResponse>(exactKey, ct);
        if (exact is not null)
            return exact;

        foreach (var candidateLimit in CandleCountGrid.ForClass(tfInfo.Class).Where(value => value > limit))
        {
            var candidateKey = BuildCacheKey(symbol, tfInfo, candidateLimit, exchange);
            var candidate = await _cache.GetAsync<ChartResponse>(candidateKey, ct);
            if (!CanSatisfyFromCachedWindow(candidate, limit))
                continue;

            var sliced = SliceCachedWindow(candidate!, limit);
            await _cache.SetAsync(exactKey, sliced, CacheTtlFor(tfInfo, fullCoverage: true), ct);
            return sliced;
        }

        return null;
    }

    private static bool CanSatisfyFromCachedWindow(ChartResponse? candidate, int limit)
    {
        return candidate is not null
            && !string.Equals(candidate.Status, "pending", StringComparison.OrdinalIgnoreCase)
            && candidate.Candles.Count >= limit
            && candidate.Meta.Available >= limit;
    }

    private static ChartResponse SliceCachedWindow(ChartResponse source, int limit)
    {
        var candles = source.Candles.Count == limit
            ? source.Candles
            : source.Candles.Skip(source.Candles.Count - limit).ToArray();

        return new ChartResponse
        {
            Symbol = source.Symbol,
            Timeframe = source.Timeframe,
            Limit = limit,
            Candles = candles,
            Meta = new ChartMetaDto
            {
                Requested = limit,
                Available = candles.Count,
                FromMs = candles.Count > 0 ? candles[0].T : 0L,
                ToMs = candles.Count > 0 ? candles[^1].T : 0L,
                Coverage = candles.Count >= limit ? "full" : source.Meta.Coverage,
            },
            Status = candles.Count >= limit ? "ok" : source.Status,
            RetryAfterMs = candles.Count >= limit ? null : source.RetryAfterMs,
        };
    }

    private string BuildCacheKey(string symbol, TimeframeInfo tfInfo, int limit, string exchange)
    {
        return string.Format(ChartKeyFmt, exchange, symbol, tfInfo.BybitInterval, limit);
    }

    private TimeSpan CacheTtlFor(TimeframeInfo tfInfo, bool fullCoverage)
    {
        // Partial/degraded results expire quickly so they get refreshed soon.
        if (!fullCoverage)
            return TimeSpan.FromSeconds(15);

        return tfInfo.Class switch
        {
            TimeframeClass.Heavy  => TimeSpan.FromSeconds(_settings.ChartCacheTtlHeavySeconds),
            TimeframeClass.Medium => TimeSpan.FromSeconds(_settings.ChartCacheTtlMediumSeconds),
            TimeframeClass.Light  => TimeSpan.FromSeconds(_settings.ChartCacheTtlLightSeconds),
            _                     => TimeSpan.FromSeconds(30),
        };
    }
}
