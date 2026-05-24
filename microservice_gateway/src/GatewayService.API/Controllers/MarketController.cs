using GatewayService.API.Clients.Market;
using GatewayService.API.DTOs.Requests;
using GatewayService.API.DTOs;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using GatewayService.API.Middleware;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text;

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
        [FromQuery] string? collection = null,
        CancellationToken ct = default)
    {
        var filteredSymbols = string.IsNullOrWhiteSpace(symbols)
            ? Array.Empty<string>()
            : symbols.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

        var result = await _market.GetTickersAsync(page, pageSize, search, sortBy, sortDir, filteredSymbols, collection, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid tickers request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=15, stale-while-revalidate=45";
        return Ok(result.Value);
    }

    [HttpGet("trending")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetTrending(
        [FromQuery] int limit = 5,
        [FromQuery] string? symbols = null,
        CancellationToken ct = default)
    {
        var filteredSymbols = string.IsNullOrWhiteSpace(symbols)
            ? Array.Empty<string>()
            : symbols.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

        var result = await _market.GetTickersAsync(1, Math.Clamp(limit, 1, 100), null, null, null, filteredSymbols, "trending", ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid trending request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=15, stale-while-revalidate=45";
        return Ok(result.Value);
    }

    [HttpGet("top-movers")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetTopMovers(
        [FromQuery] int limit = 5,
        [FromQuery] string? symbols = null,
        CancellationToken ct = default)
    {
        var filteredSymbols = string.IsNullOrWhiteSpace(symbols)
            ? Array.Empty<string>()
            : symbols.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

        var result = await _market.GetTickersAsync(1, Math.Clamp(limit, 1, 100), null, null, null, filteredSymbols, "top-movers", ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid top movers request", HttpContext.GetCorrelationId()));
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

    [HttpGet("quotes/realtime")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetRealtimeQuotes(
        [FromQuery] string? symbols = null,
        [FromQuery] string? symbol = null,
        [FromQuery] string? exchange = null,
        CancellationToken ct = default)
    {
        var requestedSymbols = string.IsNullOrWhiteSpace(symbols)
            ? Array.Empty<string>()
            : symbols.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

        if (requestedSymbols.Length == 0 && !string.IsNullOrWhiteSpace(symbol))
        {
            requestedSymbols = [symbol.Trim()];
        }

        var result = await _market.GetRealtimeQuotesAsync(requestedSymbols, exchange, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid realtime quotes request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=1, stale-while-revalidate=2";
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

    [HttpGet("convert")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> Convert(
        [FromQuery(Name = "from")] string? from,
        [FromQuery(Name = "to")] string? to,
        [FromQuery] decimal amount = 1,
        CancellationToken ct = default)
    {
        var fromAsset = string.IsNullOrWhiteSpace(from)
            ? Request.Query["fromAsset"].ToString()
            : from;
        var toAsset = string.IsNullOrWhiteSpace(to)
            ? Request.Query["toAsset"].ToString()
            : to;

        var result = await _market.GetConverterQuoteAsync(fromAsset, toAsset, amount, ct);
        if (!result.IsSuccess || result.Value is null)
        {
            return BadRequest(ErrorResponse.BadRequest(result.Error ?? "Invalid convert request", HttpContext.GetCorrelationId()));
        }

        Response.Headers["Cache-Control"] = "public, max-age=10, stale-while-revalidate=20";
        return Ok(new MarketConvertResponse
        {
            From = result.Value.FromAsset,
            To = result.Value.ToAsset,
            Amount = result.Value.Amount,
            Rate = result.Value.Rate,
            ConvertedAmount = result.Value.ConvertedAmount,
            SourceLabel = result.Value.Source,
            UpdatedAt = result.Value.UpdatedAt,
        });
    }

    /// <summary>
    /// Returns OHLCV candlestick data.
    ///
    /// If data is not yet available (first request for a symbol/timeframe),
    /// the gateway blocks until the data-service finishes its synchronous
    /// ingest. If the ingest does not complete within the budget the
    /// response is a 503 SERVICE_BUSY — the client never sees a "pending"
    /// status.
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
    /// <param name="exchange">
    /// Exchange to fetch candles from. Supported: "bybit" (default), "binance", "kraken".
    /// Unknown values fall back to bybit. Each exchange has its own data-service table
    /// and independent Redis cache namespace.
    /// </param>
    /// <param name="ct">Cancellation token (request abort).</param>
    /// <response code="200">Chart response (status is "ok" or "partial").</response>
    /// <response code="400">Invalid symbol, timeframe, or limit.</response>
    /// <response code="503">Downstream chart data is temporarily unavailable.</response>
    [HttpGet("chart")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status503ServiceUnavailable)]
    public async Task<IActionResult> GetChart(
        [FromQuery] string symbol,
        [FromQuery] string timeframe,
        [FromQuery] int limit,
        [FromQuery] string? exchange,
        CancellationToken ct)
    {
        var correlationId = HttpContext.GetCorrelationId();
        var exchangeKey = DataServiceClient.NormalizeExchange(exchange);

        var result = await _chart.GetChartAsync(symbol, timeframe, limit, exchangeKey, ct);

        if (!result.IsSuccess)
        {
            var error = result.Error ?? "Invalid request";
            var errorCode = GetChartErrorCode(error);
            if (IsChartServiceUnavailableCode(errorCode))
            {
                return StatusCode(StatusCodes.Status503ServiceUnavailable,
                    ErrorResponse.ServiceUnavailable("market_chart", correlationId) with
                    {
                        Code = errorCode,
                        Detail = GetChartErrorDetail(error),
                    });
            }

            return BadRequest(ErrorResponse.BadRequest(error, correlationId));
        }

        if (result.Value is null)
        {
            return StatusCode(StatusCodes.Status503ServiceUnavailable,
                ErrorResponse.ServiceUnavailable("market_chart", correlationId));
        }

        ApplyChartHttpCaching(result.Value);

        var etag = BuildChartEtag(result.Value);
        Response.Headers[HeaderNames.ETag] = etag;

        if (result.Value.Meta.ToMs > 0)
        {
            Response.Headers[HeaderNames.LastModified] =
                DateTimeOffset.FromUnixTimeMilliseconds(result.Value.Meta.ToMs).ToString("R");
        }

        if (Request.Headers.TryGetValue(HeaderNames.IfNoneMatch, out var ifNoneMatch)
            && ifNoneMatch.Any(value => string.Equals(value, etag, StringComparison.Ordinal)))
        {
            return StatusCode(StatusCodes.Status304NotModified);
        }

        return Ok(result.Value);
    }

    /// <summary>
    /// Returns OHLCV chart data for the same symbol/timeframe across multiple
    /// exchanges, fetched in parallel. Used by the frontend "Compare exchanges"
    /// overlay so the client doesn't need 3 separate roundtrips.
    /// </summary>
    /// <param name="symbol">Trading pair, e.g. BTCUSDT.</param>
    /// <param name="timeframe">Timeframe id from /config.</param>
    /// <param name="limit">Number of candles per exchange.</param>
    /// <param name="exchanges">Comma-separated list of exchanges. Default: "bybit,binance,kraken".</param>
    /// <param name="ct">Cancellation token.</param>
    /// <response code="200">
    /// Object with <c>items</c> (exchange → ChartResponse) and <c>errors</c>
    /// (exchange → error code) so partial failures don't blank the whole comparison.
    /// </response>
    /// <response code="400">No valid exchanges, or invalid symbol/timeframe/limit.</response>
    [HttpGet("chart/compare")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(typeof(ErrorResponse), StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> GetChartCompare(
        [FromQuery] string symbol,
        [FromQuery] string timeframe,
        [FromQuery] int limit,
        [FromQuery] string? exchanges = null,
        CancellationToken ct = default)
    {
        var correlationId = HttpContext.GetCorrelationId();

        var requested = string.IsNullOrWhiteSpace(exchanges)
            ? new[] { "bybit", "binance", "kraken" }
            : exchanges
                .Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries)
                .Select(static e => DataServiceClient.NormalizeExchange(e))
                .Distinct()
                .ToArray();

        if (requested.Length == 0)
        {
            return BadRequest(ErrorResponse.BadRequest(
                "At least one valid exchange must be requested",
                correlationId));
        }

        // Cap parallel fan-out so a malicious caller can't spawn dozens of
        // concurrent ingests by passing a huge exchanges list.
        if (requested.Length > 3)
        {
            requested = requested.Take(3).ToArray();
        }

        var tasks = requested
            .Select(async ex =>
            {
                var result = await _chart.GetChartAsync(symbol, timeframe, limit, ex, ct);
                return (Exchange: ex, Result: result);
            })
            .ToArray();

        var completed = await Task.WhenAll(tasks);

        var items  = new Dictionary<string, ChartResponse>(StringComparer.OrdinalIgnoreCase);
        var errors = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        foreach (var (ex, result) in completed)
        {
            if (result.IsSuccess && result.Value is not null)
            {
                items[ex] = result.Value;
            }
            else
            {
                errors[ex] = result.Error ?? "DATA_SOURCE_UNAVAILABLE: unknown error";
            }
        }

        // If everything failed, surface a clean 400/503 so the client can
        // handle it instead of receiving an empty success.
        if (items.Count == 0)
        {
            return StatusCode(StatusCodes.Status503ServiceUnavailable,
                ErrorResponse.ServiceUnavailable("market_chart_compare", correlationId));
        }

        Response.Headers["Cache-Control"] = "public, max-age=15, stale-while-revalidate=45";
        return Ok(new
        {
            symbol,
            timeframe,
            limit,
            exchanges = requested,
            items,
            errors,
        });
    }

    private static bool IsChartServiceUnavailableCode(string? errorCode)
    {
        return errorCode is "DATA_SOURCE_UNAVAILABLE" or "DOWNSTREAM_TIMEOUT" or "SERVICE_BUSY";
    }

    private static string? GetChartErrorCode(string? error)
    {
        if (string.IsNullOrWhiteSpace(error))
            return null;

        var separatorIndex = error.IndexOf(':');
        if (separatorIndex <= 0)
            return null;

        var candidate = error[..separatorIndex].Trim();
        return string.IsNullOrWhiteSpace(candidate)
            ? null
            : candidate.ToUpperInvariant();
    }

    private static string GetChartErrorDetail(string? error)
    {
        if (string.IsNullOrWhiteSpace(error))
            return "The 'market_chart' service is temporarily unavailable.";

        var separatorIndex = error.IndexOf(':');
        return separatorIndex < 0
            ? error
            : error[(separatorIndex + 1)..].Trim();
    }

    private void ApplyChartHttpCaching(ChartResponse response)
    {
        var maxAge = response.Status switch
        {
            "ok" when TimeframeMap.TryGetById(response.Timeframe, out var tfInfo)
                => tfInfo.Class switch
                {
                    TimeframeClass.Heavy => 10,
                    TimeframeClass.Medium => 30,
                    TimeframeClass.Light => 60,
                    _ => 10,
                },
            "partial" => 3,
            _ => 3,
        };

        var staleWhileRevalidate = response.Status switch
        {
            "ok" => Math.Max(maxAge * 3, 30),
            "partial" => 12,
            _ => 12,
        };

        Response.Headers[HeaderNames.CacheControl] =
            $"public, max-age={maxAge}, stale-while-revalidate={staleWhileRevalidate}";
    }

    private static string BuildChartEtag(ChartResponse response)
    {
        var seed = string.Join(':',
            response.Symbol,
            response.Timeframe,
            response.Limit,
            response.Status,
            response.Meta.Available,
            response.Meta.FromMs,
            response.Meta.ToMs,
            response.RetryAfterMs ?? 0);

        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(seed));
        return $"W/\"{System.Convert.ToHexString(hash)}\"";
    }
}
