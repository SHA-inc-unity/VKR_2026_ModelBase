using GatewayService.API.Clients.Market;
using GatewayService.API.DTOs;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using GatewayService.API.Middleware;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Market data API for Kotlin mobile clients.
///
/// No authentication required — all endpoints are public.
///
/// Endpoints:
/// - GET /api/v1/market/config   → server-authoritative symbols / timeframes / limits grid
/// - GET /api/v1/market/chart    → OHLCV candlestick data for a given symbol + timeframe
/// </summary>
[ApiController]
[Route("api/v1/market")]
public sealed class MarketController : ControllerBase
{
    private readonly IMarketServiceClient _market;
    private readonly IMarketConfigService _marketConfig;
    private readonly IChartService        _chart;

    public MarketController(
        IMarketServiceClient market,
        IMarketConfigService marketConfig,
        IChartService chart)
    {
        _market = market;
        _marketConfig = marketConfig;
        _chart        = chart;
    }

    /// <summary>
    /// Returns the server-authoritative market configuration.
    ///
    /// Kotlin clients MUST call this before constructing a /chart request.
    /// The response is cached in Redis and rarely changes (symbol list is
    /// refreshed hourly from Bybit).
    /// </summary>
    /// <response code="200">Market configuration response.</response>
    [HttpGet("config")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> GetConfig(CancellationToken ct)
    {
        var response = await _marketConfig.GetConfigAsync(ct);

        // Stale-while-revalidate: config changes at most every hour.
        Response.Headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=3540";
        return Ok(response);
    }

    [HttpGet("overview")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> GetOverview(CancellationToken ct)
    {
        var configTask = _marketConfig.GetConfigAsync(ct);
        var overviewTask = _market.GetOverviewAsync(ct);
        var trendingTask = _market.GetTrendingAsync(5, ct);

        await Task.WhenAll(configTask, overviewTask, trendingTask);

        var config = await configTask;
        var degraded = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        var overviewResult = await overviewTask;
        var overview = overviewResult.IsSuccess && overviewResult.Value is not null
            ? overviewResult.Value
            : null;
        if (overview is null)
        {
            degraded.Add("marketOverview");
        }

        var trendingResult = await trendingTask;
        var trendingAssets = trendingResult.IsSuccess && trendingResult.Value is not null
            ? trendingResult.Value.Select(item => item.Symbol).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray()
            : config.Symbols.Take(5).ToArray();
        if (!trendingResult.IsSuccess)
        {
            degraded.Add("trendingAssets");
        }

        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120";
        return Ok(new PublicMarketOverviewResponse
        {
            MarketOverview = new PublicMarketOverviewDto
            {
                TotalMarketCap = overview?.TotalMarketCapUsd ?? 0,
                BtcDominance = overview?.BtcDominance ?? 0,
                Volume24h = overview?.Volume24hUsd ?? 0,
                ActiveAssets = config.Symbols.Count,
                FearGreedValue = 0,
                FearGreedLabel = "Neutral",
            },
            TrendingAssets = trendingAssets,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                DegradedSections = degraded.ToArray(),
            }
        });
    }

    /// <summary>
    /// Returns OHLCV candlestick data.
    ///
    /// If data is not yet available (first request for a symbol/timeframe),
    /// the response will have status="pending" and a retry_after_ms hint.
    /// The background ingest is triggered automatically.
    ///
    /// If partial data is available, status="partial" with whatever candles
    /// are present. The client should retry after retry_after_ms.
    /// </summary>
    /// <param name="symbol">Trading pair, e.g. BTCUSDT.</param>
    /// <param name="timeframe">Timeframe id from /config, e.g. 5m, 1d.</param>
    /// <param name="limit">
    /// Number of candles to return. Must be one of the values from /config
    /// candle_counts for the chosen timeframe class.
    /// </param>
    /// <response code="200">Chart response (status may be ok / partial / pending).</response>
    /// <response code="400">Invalid symbol, timeframe, or limit.</response>
    [HttpGet("chart")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetChart(
        [FromQuery] string symbol,
        [FromQuery] string timeframe,
        [FromQuery] int limit,
        CancellationToken ct)
    {
        var correlationId = HttpContext.GetCorrelationId();

        var result = await _chart.GetChartAsync(symbol, timeframe, limit, ct);

        if (!result.IsSuccess)
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid request", correlationId));

        return Ok(result.Value);
    }
}
