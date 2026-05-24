using Microsoft.AspNetCore.Mvc;
using SocialService.Application.Interfaces.Services;

namespace SocialService.API.Controllers;

/// <summary>
/// Internal routes (X-Internal-Api-Key) so other services (notifications)
/// can answer "who authored this comment" and "who favorited this symbol"
/// without going through the user-facing gateway.
/// </summary>
[ApiController]
[Route("internal")]
public sealed class InternalController : ControllerBase
{
    private readonly ICommentsAppService _comments;
    private readonly IFavoritesAppService _favorites;
    private readonly IConfiguration _config;

    public InternalController(
        ICommentsAppService comments,
        IFavoritesAppService favorites,
        IConfiguration config)
    {
        _comments = comments;
        _favorites = favorites;
        _config = config;
    }

    [HttpGet("comments/{id:guid}/author")]
    public async Task<IActionResult> CommentAuthor(Guid id, CancellationToken ct)
    {
        if (!IsAuthorized()) return Unauthorized();
        var author = await _comments.GetAuthorAsync(id, ct);
        return author is null ? NotFound() : Ok(new { authorId = author });
    }

    [HttpGet("favorites/users-by-symbol/{symbol}")]
    public async Task<IActionResult> FavoritesBySymbol(string symbol, CancellationToken ct)
    {
        if (!IsAuthorized()) return Unauthorized();
        var users = await _favorites.UsersBySymbolAsync(symbol, ct);
        return Ok(new { symbol = symbol.ToUpperInvariant(), users });
    }

    private bool IsAuthorized()
    {
        var expected = _config["InternalApi:ApiKey"];
        if (string.IsNullOrWhiteSpace(expected)) return false;
        Request.Headers.TryGetValue("X-Internal-Api-Key", out var provided);
        return provided == expected;
    }
}
