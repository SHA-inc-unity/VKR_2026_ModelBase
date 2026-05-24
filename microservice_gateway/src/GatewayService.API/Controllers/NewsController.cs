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
    public async Task<IActionResult> GetList([FromQuery] int limit = 20, CancellationToken ct = default)
    {
        var response = await BuildResponseAsync(limit, ct);
        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300";
        return Ok(response);
    }

    [HttpGet("home")]
    [AllowAnonymous]
    public async Task<IActionResult> GetHome(CancellationToken ct = default)
    {
        var response = await BuildResponseAsync(limit: 3, ct);
        Response.Headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300";
        return Ok(response);
    }

    private async Task<NewsListResponse> BuildResponseAsync(int limit, CancellationToken ct)
    {
        limit = Math.Clamp(limit, 1, 100);
        var result = await _news.GetLatestAsync(limit, ct);
        var items = result.IsSuccess
            ? (result.Value ?? []).OrderByDescending(item => item.PublishedAt).Take(limit).ToArray()
            : Array.Empty<NewsItemDto>();

        var response = new NewsListResponse
        {
            Items = items,
            Total = items.Length,
            Degraded = !result.IsSuccess
        };

        return response;
    }
}
