using System.Text.Json;
using GatewayService.API.Clients.Social;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Forwards favorites / comments / likes to the Social Service.
/// </summary>
[ApiController]
[Route("api/social")]
public sealed class SocialController : ControllerBase
{
    private readonly ISocialServiceClient _social;
    public SocialController(ISocialServiceClient social) => _social = social;

    // ── Favorites ────────────────────────────────────────────────────────────
    [Authorize]
    [HttpGet("favorites")]
    public Task<IActionResult> ListFavorites(CancellationToken ct) =>
        Forward(HttpMethod.Get, "api/social/favorites", requireBearer: true, ct: ct);

    [Authorize]
    [HttpPut("favorites/{symbol}")]
    public Task<IActionResult> AddFavorite(string symbol, CancellationToken ct) =>
        Forward(HttpMethod.Put, $"api/social/favorites/{Uri.EscapeDataString(symbol)}", requireBearer: true, ct: ct);

    [Authorize]
    [HttpDelete("favorites/{symbol}")]
    public Task<IActionResult> RemoveFavorite(string symbol, CancellationToken ct) =>
        Forward(HttpMethod.Delete, $"api/social/favorites/{Uri.EscapeDataString(symbol)}", requireBearer: true, ct: ct);

    // ── Comments ─────────────────────────────────────────────────────────────
    [AllowAnonymous]
    [HttpGet("comments")]
    public Task<IActionResult> ListComments(CancellationToken ct) =>
        Forward(HttpMethod.Get, "api/social/comments", requireBearer: false,
            query: Request.QueryString.HasValue ? Request.QueryString.Value!.TrimStart('?') : null,
            ct: ct);

    [Authorize]
    [HttpPost("comments")]
    public Task<IActionResult> PostComment([FromBody] JsonElement body, CancellationToken ct) =>
        Forward(HttpMethod.Post, "api/social/comments", requireBearer: true, body: body, ct: ct);

    [Authorize]
    [HttpPatch("comments/{id:guid}")]
    public Task<IActionResult> PatchComment(Guid id, [FromBody] JsonElement body, CancellationToken ct) =>
        Forward(HttpMethod.Patch, $"api/social/comments/{id}", requireBearer: true, body: body, ct: ct);

    [Authorize]
    [HttpDelete("comments/{id:guid}")]
    public Task<IActionResult> DeleteComment(Guid id, CancellationToken ct) =>
        Forward(HttpMethod.Delete, $"api/social/comments/{id}", requireBearer: true, ct: ct);

    [Authorize]
    [HttpPost("comments/{id:guid}/like")]
    public Task<IActionResult> LikeComment(Guid id, CancellationToken ct) =>
        Forward(HttpMethod.Post, $"api/social/comments/{id}/like", requireBearer: true, ct: ct);

    [Authorize]
    [HttpDelete("comments/{id:guid}/like")]
    public Task<IActionResult> UnlikeComment(Guid id, CancellationToken ct) =>
        Forward(HttpMethod.Delete, $"api/social/comments/{id}/like", requireBearer: true, ct: ct);

    // ── Sentiment (per-coin bullish/bearish voting) ───────────────────────────
    [AllowAnonymous]
    [HttpGet("sentiment")]
    public Task<IActionResult> GetSentiment(CancellationToken ct) =>
        Forward(HttpMethod.Get, "api/social/sentiment", requireBearer: false,
            query: Request.QueryString.HasValue ? Request.QueryString.Value!.TrimStart('?') : null,
            ct: ct);

    [Authorize]
    [HttpPost("sentiment")]
    public Task<IActionResult> PostSentiment([FromBody] JsonElement body, CancellationToken ct) =>
        Forward(HttpMethod.Post, "api/social/sentiment", requireBearer: true, body: body, ct: ct);

    // ── Internals ─────────────────────────────────────────────────────────────
    private async Task<IActionResult> Forward(
        HttpMethod method,
        string path,
        bool requireBearer,
        string? query = null,
        JsonElement? body = null,
        CancellationToken ct = default)
    {
        // Anonymous routes (ListComments etc.) still want to opportunistically
        // forward the bearer token when one is present, so downstream Social
        // can fill `likedByMe` / personalised projections for the caller.
        // Without this the social service sees no Authorization header and
        // every refresh of the comments list returns likedByMe=false for the
        // signed-in user, which makes their own ❤️ disappear on reload.
        var token = GetRawToken();
        if (requireBearer && token is null) return Unauthorized();

        try
        {
            var resp = await _social.ForwardAsync(method, path, query, body, token, ct);
            return new ContentResult
            {
                StatusCode = resp.StatusCode,
                Content = resp.Content,
                ContentType = resp.ContentType,
            };
        }
        catch
        {
            return StatusCode(503, new { error = "social_service_unavailable" });
        }
    }

    private string? GetRawToken()
    {
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is null || !header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;
        return header["Bearer ".Length..].Trim();
    }
}
