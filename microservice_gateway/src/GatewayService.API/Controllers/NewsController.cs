using GatewayService.API.Clients.News;
using GatewayService.API.DTOs.Responses;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// News feed — publicly accessible.
/// </summary>
[ApiController]
[Route("api/news")]
public sealed class NewsController : ControllerBase
{
    private readonly INewsServiceClient _news;

    public NewsController(INewsServiceClient news) => _news = news;

    /// <summary>Returns the latest news items.</summary>
    /// <param name="limit">Max number of items to return (default 20, max 100).</param>
    [HttpGet]
    [AllowAnonymous]
    public async Task<IActionResult> GetList([FromQuery] int limit = 20, [FromQuery] string? tag = null, CancellationToken ct = default)
    {
        var response = await BuildResponseAsync(limit, tag, ct);
        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300";
        return Ok(response);
    }

    [HttpGet("home")]
    [AllowAnonymous]
    public async Task<IActionResult> GetHome([FromQuery] int limit = 3, [FromQuery] string? tag = null, CancellationToken ct = default)
    {
        var response = await BuildResponseAsync(limit, tag, ct);
        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300";
        return Ok(response);
    }

    private async Task<NewsListResponse> BuildResponseAsync(int limit, string? tag, CancellationToken ct)
    {
        limit = Math.Clamp(limit, 1, 100);
        var normalizedTag = string.IsNullOrWhiteSpace(tag) ? null : tag.Trim();
        var result = await _news.GetLatestAsync(limit, ct);
        IEnumerable<NewsItemDto> items = result.IsSuccess ? result.Value ?? [] : [];

        if (!string.IsNullOrWhiteSpace(normalizedTag))
        {
            items = items.Where(item => item.Tags.Contains(normalizedTag, StringComparer.OrdinalIgnoreCase));
        }

        var materializedItems = items.OrderByDescending(item => item.PublishedAt).Take(limit).ToArray();

        var response = new NewsListResponse
        {
            Items = materializedItems,
            Total = materializedItems.Length,
            Degraded = !result.IsSuccess
        };

        return response;
    }
}
