using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using NewsService.Application.DTOs;
using NewsService.Application.Services;

namespace NewsService.API.Controllers;

[ApiController]
[Route("api/news")]
[AllowAnonymous]
public sealed class NewsController : ControllerBase
{
    private readonly INewsAppService _service;

    public NewsController(INewsAppService service) => _service = service;

    [HttpGet]
    public async Task<ActionResult<NewsListResponse>> List(
        [FromQuery] string? symbol,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 30,
        CancellationToken ct = default)
    {
        var res = await _service.ListAsync(symbol, page, pageSize, ct);
        return Ok(res);
    }

    [HttpGet("{id:guid}")]
    public async Task<ActionResult<NewsArticleResponse>> Get(Guid id, CancellationToken ct)
    {
        var res = await _service.GetAsync(id, ct);
        return res is null ? NotFound() : Ok(res);
    }
}
