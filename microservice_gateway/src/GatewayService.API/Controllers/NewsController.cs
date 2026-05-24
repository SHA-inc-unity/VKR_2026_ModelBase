using GatewayService.API.Clients.News;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// News feed proxy — forwards to microservice_news. Publicly accessible.
/// </summary>
[ApiController]
[Route("api/news")]
[AllowAnonymous]
public sealed class NewsController : ControllerBase
{
    private readonly INewsHttpProxyClient _news;

    public NewsController(INewsHttpProxyClient news) => _news = news;

    [HttpGet]
    public async Task<IActionResult> List(
        [FromQuery] string? symbol,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 30,
        CancellationToken ct = default)
    {
        var query = BuildQuery(symbol, page, pageSize);
        var resp = await _news.ForwardAsync(HttpMethod.Get, "api/news", query, ct: ct);
        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300";
        return new ContentResult
        {
            StatusCode = resp.StatusCode,
            Content = resp.Content,
            ContentType = resp.ContentType,
        };
    }

    [HttpGet("home")]
    public async Task<IActionResult> Home([FromQuery] int limit = 3, CancellationToken ct = default)
    {
        var query = $"page=1&pageSize={Math.Clamp(limit, 1, 100)}";
        var resp = await _news.ForwardAsync(HttpMethod.Get, "api/news", query, ct: ct);
        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300";
        return new ContentResult
        {
            StatusCode = resp.StatusCode,
            Content = resp.Content,
            ContentType = resp.ContentType,
        };
    }

    [HttpGet("{id:guid}")]
    public async Task<IActionResult> Get(Guid id, CancellationToken ct)
    {
        var resp = await _news.ForwardAsync(HttpMethod.Get, $"api/news/{id}", ct: ct);
        return new ContentResult
        {
            StatusCode = resp.StatusCode,
            Content = resp.Content,
            ContentType = resp.ContentType,
        };
    }

    private static string BuildQuery(string? symbol, int page, int pageSize)
    {
        var parts = new List<string>
        {
            $"page={Math.Max(1, page)}",
            $"pageSize={Math.Clamp(pageSize, 1, 100)}",
        };
        if (!string.IsNullOrWhiteSpace(symbol)) parts.Add($"symbol={Uri.EscapeDataString(symbol)}");
        return string.Join('&', parts);
    }
}
