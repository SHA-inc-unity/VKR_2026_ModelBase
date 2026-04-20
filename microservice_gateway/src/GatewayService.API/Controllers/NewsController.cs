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
        limit = Math.Clamp(limit, 1, 100);
        var result = await _news.GetLatestAsync(limit, ct);

        var response = new NewsListResponse
        {
            Items = result.IsSuccess ? result.Value ?? [] : [],
            Total = result.IsSuccess ? result.Value?.Count ?? 0 : 0,
            Degraded = !result.IsSuccess
        };

        return Ok(response);
    }
}
