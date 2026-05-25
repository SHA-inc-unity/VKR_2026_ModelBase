using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using SocialService.Application.DTOs.Requests;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Application.Interfaces.Services;

namespace SocialService.API.Controllers;

[ApiController]
[Route("api/social/comments")]
public sealed class CommentsController : ControllerBase
{
    private readonly ICommentsAppService _svc;

    public CommentsController(ICommentsAppService svc) => _svc = svc;

    [HttpGet]
    [AllowAnonymous]
    public async Task<IActionResult> List(
        [FromQuery] string targetType,
        [FromQuery] string targetId,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 50,
        [FromQuery] string? sort = null,
        CancellationToken ct = default)
    {
        Guid? viewer = TryCurrentUserId();
        var sortMode = ParseSort(sort);
        var result = await _svc.ListAsync(targetType, targetId, page, pageSize, sortMode, viewer, ct);
        return Ok(result);
    }

    private static CommentSortMode ParseSort(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return CommentSortMode.Top24h;
        return raw.Trim().ToLowerInvariant() switch
        {
            "new" => CommentSortMode.New,
            "top" => CommentSortMode.Top,
            "top24h" or "hot" or "trending" => CommentSortMode.Top24h,
            _ => CommentSortMode.Top24h,
        };
    }

    [HttpPost]
    [Authorize]
    public async Task<IActionResult> Create([FromBody] CreateCommentRequest request, CancellationToken ct)
    {
        var userId = CurrentUserId();
        var result = await _svc.CreateAsync(userId, request, ct);
        return Ok(result);
    }

    [HttpPatch("{id:guid}")]
    [Authorize]
    public async Task<IActionResult> Update(Guid id, [FromBody] UpdateCommentRequest request, CancellationToken ct)
    {
        var userId = CurrentUserId();
        var isAdmin = IsAdmin();
        var result = await _svc.UpdateAsync(userId, id, request, isAdmin, ct);
        return Ok(result);
    }

    [HttpDelete("{id:guid}")]
    [Authorize]
    public async Task<IActionResult> Delete(Guid id, CancellationToken ct)
    {
        var userId = CurrentUserId();
        var isAdmin = IsAdmin();
        await _svc.DeleteAsync(userId, id, isAdmin, ct);
        return NoContent();
    }

    [HttpPost("{id:guid}/like")]
    [Authorize]
    public async Task<IActionResult> Like(Guid id, CancellationToken ct)
    {
        var userId = CurrentUserId();
        await _svc.LikeAsync(userId, id, ct);
        return NoContent();
    }

    [HttpDelete("{id:guid}/like")]
    [Authorize]
    public async Task<IActionResult> Unlike(Guid id, CancellationToken ct)
    {
        var userId = CurrentUserId();
        await _svc.UnlikeAsync(userId, id, ct);
        return NoContent();
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

    private bool IsAdmin()
    {
        return User.IsInRole("admin") || User.Claims.Any(c =>
            (c.Type == ClaimTypes.Role || c.Type == "role" || c.Type == "roles")
            && string.Equals(c.Value, "admin", StringComparison.OrdinalIgnoreCase));
    }
}
