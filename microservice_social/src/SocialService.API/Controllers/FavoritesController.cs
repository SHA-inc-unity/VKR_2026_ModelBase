using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using SocialService.Application.Interfaces.Services;

namespace SocialService.API.Controllers;

[ApiController]
[Route("api/social/favorites")]
[Authorize]
public sealed class FavoritesController : ControllerBase
{
    private readonly IFavoritesAppService _svc;

    public FavoritesController(IFavoritesAppService svc) => _svc = svc;

    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct)
    {
        var userId = CurrentUserId();
        var result = await _svc.ListAsync(userId, ct);
        return Ok(result);
    }

    [HttpPut("{symbol}")]
    public async Task<IActionResult> Add(string symbol, CancellationToken ct)
    {
        var userId = CurrentUserId();
        await _svc.AddAsync(userId, symbol, ct);
        return NoContent();
    }

    [HttpDelete("{symbol}")]
    public async Task<IActionResult> Remove(string symbol, CancellationToken ct)
    {
        var userId = CurrentUserId();
        await _svc.RemoveAsync(userId, symbol, ct);
        return NoContent();
    }

    private Guid CurrentUserId()
    {
        var idClaim = User.FindFirstValue(ClaimTypes.NameIdentifier)
            ?? User.FindFirstValue("sub")
            ?? User.FindFirstValue("nameid");
        if (string.IsNullOrWhiteSpace(idClaim) || !Guid.TryParse(idClaim, out var guid))
            throw new UnauthorizedAccessException("user id claim missing");
        return guid;
    }
}
