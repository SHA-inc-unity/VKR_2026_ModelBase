using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using AccountService.API.Extensions;
using AccountService.Application.DTOs.Requests;
using AccountService.Application.Interfaces.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;

namespace AccountService.API.Controllers;

[ApiController]
[Route("api/account")]
public sealed class AccountController : ControllerBase
{
    private readonly IAccountService _accountService;

    public AccountController(IAccountService accountService) =>
        _accountService = accountService;

    // ── Public endpoints ──────────────────────────────────────────────────────

    [HttpPost("register")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status409Conflict)]
    [ProducesResponseType(StatusCodes.Status422UnprocessableEntity)]
    public async Task<IActionResult> Register(
        [FromBody] RegisterRequest request,
        CancellationToken ct)
    {
        var result = await _accountService.RegisterAsync(request, GetIp(), GetUserAgent(), ct);
        return Ok(result);
    }

    [HttpPost("login")]
    [EnableRateLimiting(ServiceCollectionExtensions.AuthRateLimitPolicy)]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status429TooManyRequests)]
    public async Task<IActionResult> Login(
        [FromBody] LoginRequest request,
        CancellationToken ct)
    {
        var result = await _accountService.LoginAsync(request, GetIp(), GetUserAgent(), ct);
        return Ok(result);
    }

    [HttpPost("refresh")]
    [EnableRateLimiting(ServiceCollectionExtensions.AuthRateLimitPolicy)]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status429TooManyRequests)]
    public async Task<IActionResult> Refresh(
        [FromBody] RefreshTokenRequest request,
        CancellationToken ct)
    {
        var result = await _accountService.RefreshAsync(request, GetIp(), GetUserAgent(), ct);
        return Ok(result);
    }

    [HttpPost("logout")]
    [Authorize]
    [ProducesResponseType(StatusCodes.Status204NoContent)]
    public async Task<IActionResult> Logout(
        [FromBody] LogoutRequest request,
        CancellationToken ct)
    {
        var userId = GetCurrentUserId();
        await _accountService.LogoutAsync(
            request, userId, GetCurrentTokenJti(), GetCurrentTokenExpiry(), ct);
        return NoContent();
    }

    // ── Protected endpoints ───────────────────────────────────────────────────

    [HttpGet("me")]
    [Authorize]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    public async Task<IActionResult> Me(CancellationToken ct)
    {
        var result = await _accountService.GetCurrentUserAsync(GetCurrentUserId(), ct);
        return Ok(result);
    }

    [HttpPut("profile")]
    [Authorize]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status409Conflict)]
    public async Task<IActionResult> UpdateProfile(
        [FromBody] UpdateProfileRequest request,
        CancellationToken ct)
    {
        var result = await _accountService.UpdateProfileAsync(GetCurrentUserId(), request, ct);
        return Ok(result);
    }

    [HttpGet("settings")]
    [Authorize]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> GetSettings(CancellationToken ct)
    {
        var result = await _accountService.GetSettingsAsync(GetCurrentUserId(), ct);
        return Ok(result);
    }

    [HttpPut("settings")]
    [Authorize]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> UpdateSettings(
        [FromBody] UpdateSettingsRequest request,
        CancellationToken ct)
    {
        var result = await _accountService.UpdateSettingsAsync(GetCurrentUserId(), request, ct);
        return Ok(result);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private Guid GetCurrentUserId()
    {
        var sub = User.FindFirstValue(ClaimTypes.NameIdentifier)
            ?? User.FindFirstValue("sub");
        return Guid.TryParse(sub, out var id)
            ? id
            : throw new UnauthorizedAccessException("Invalid token subject claim.");
    }

    /// <summary>JTI of the access token presented on this request (for revocation), or null.</summary>
    private string? GetCurrentTokenJti() => User.FindFirstValue(JwtRegisteredClaimNames.Jti);

    /// <summary>Expiry of the access token presented on this request, or null if absent/malformed.</summary>
    private DateTimeOffset? GetCurrentTokenExpiry()
    {
        // Read exp from the RAW bearer token so it is independent of inbound claim
        // mapping (the mapped principal may not surface the "exp" claim verbatim).
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is not null &&
            header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
        {
            var raw = header["Bearer ".Length..].Trim();
            var handler = new JwtSecurityTokenHandler();
            if (handler.CanReadToken(raw))
            {
                var validTo = handler.ReadJwtToken(raw).ValidTo;
                if (validTo != DateTime.MinValue)
                    return new DateTimeOffset(DateTime.SpecifyKind(validTo, DateTimeKind.Utc));
            }
        }
        // Fallback: the standard/mapped exp claim if present.
        var exp = User.FindFirstValue(JwtRegisteredClaimNames.Exp);
        return long.TryParse(exp, out var seconds)
            ? DateTimeOffset.FromUnixTimeSeconds(seconds)
            : null;
    }

    private string? GetIp() =>
        HttpContext.Connection.RemoteIpAddress?.ToString();

    private string? GetUserAgent() =>
        Request.Headers.UserAgent.FirstOrDefault();
}
