using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using SocialService.Application.DTOs.Requests;
using SocialService.Application.Interfaces.Services;

namespace SocialService.API.Controllers;

[ApiController]
[Route("api/social/sentiment")]
public sealed class SentimentController : ControllerBase
{
    private readonly IAssetSentimentAppService _svc;

    public SentimentController(IAssetSentimentAppService svc) => _svc = svc;

    [HttpGet]
    [AllowAnonymous]
    public async Task<IActionResult> Get(
        [FromQuery] string targetType,
        [FromQuery] string targetId,
        CancellationToken ct = default)
    {
        Guid? viewer = TryCurrentUserId();
        var result = await _svc.GetAsync(targetType, targetId, viewer, ct);
        return Ok(result);
    }

    [HttpPost]
    [Authorize]
    public async Task<IActionResult> Vote([FromBody] SentimentVoteRequest request, CancellationToken ct)
    {
        var userId = CurrentUserId();
        var result = await _svc.VoteAsync(userId, request.TargetType, request.TargetId, request.Vote, ct);
        return Ok(result);
    }

    private Guid CurrentUserId() =>
        TryCurrentUserId() ?? throw new UnauthorizedAccessException("user id claim missing");

    private Guid? TryCurrentUserId()
    {
        var idClaim = User.FindFirstValue(ClaimTypes.NameIdentifier)
            ?? User.FindFirstValue("sub")
            ?? User.FindFirstValue("nameid");
        return Guid.TryParse(idClaim, out var guid) ? guid : null;
    }
}
