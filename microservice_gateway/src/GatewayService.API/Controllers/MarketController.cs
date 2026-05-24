using GatewayService.API.Clients.Market;
using GatewayService.API.DTOs.Requests;
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
        var result = await _market.GetPublicOverviewAsync(5, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return StatusCode(StatusCodes.Status503ServiceUnavailable,
                ErrorResponse.ServiceUnavailable("market_overview", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=120";
        return Ok(result.Value);
    }

    [HttpGet("tickers")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetTickers(
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 25,
        [FromQuery] string? search = null,
        [FromQuery] string? sortBy = null,
        [FromQuery] string? sortDir = null,
        [FromQuery] string? symbols = null,
        CancellationToken ct = default)
    {
        var filteredSymbols = string.IsNullOrWhiteSpace(symbols)
            ? Array.Empty<string>()
            : symbols.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

        var result = await _market.GetTickersAsync(page, pageSize, search, sortBy, sortDir, filteredSymbols, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid tickers request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=15, stale-while-revalidate=45";
        return Ok(result.Value);
    }

    [HttpPost("quotes/batch")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetBatchQuotes([FromBody] BatchMarketQuotesRequest request, CancellationToken ct)
    {
        var result = await _market.GetQuotesAsync(request.Symbols, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid quotes request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=20";
        return Ok(result.Value);
    }

    [HttpGet("converter/quote")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetConverterQuote(
        [FromQuery] string fromAsset,
        [FromQuery] string toAsset,
        [FromQuery] decimal amount = 1,
        CancellationToken ct = default)
    {
        var result = await _market.GetConverterQuoteAsync(fromAsset, toAsset, amount, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid converter quote request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=20";
        return Ok(result.Value);
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
