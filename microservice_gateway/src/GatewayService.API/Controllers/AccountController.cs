using System.Net.Http;
using GatewayService.API.Clients.Account;
using GatewayService.API.DTOs;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using GatewayService.API.Middleware;
using System.Text.Json;

namespace GatewayService.API.Controllers;

/// <summary>
/// Proxies account/profile requests to the Account Service.
/// </summary>
[ApiController]
[Route("api/account")]
public sealed class AccountController : ControllerBase
{
    private readonly IAccountServiceClient _account;
    private readonly IAccountAuthProxyClient _authProxy;

    public AccountController(
        IAccountServiceClient account,
        IAccountAuthProxyClient authProxy)
    {
        _account = account;
        _authProxy = authProxy;
    }

    [AllowAnonymous]
    [HttpPost("register")]
    public Task<IActionResult> Register([FromBody] JsonElement request, CancellationToken ct) =>
        ForwardAuthAsync(HttpMethod.Post, "api/account/register", request, requiresBearer: false, ct);

    [AllowAnonymous]
    [HttpPost("login")]
    public Task<IActionResult> Login([FromBody] JsonElement request, CancellationToken ct) =>
        ForwardAuthAsync(HttpMethod.Post, "api/account/login", request, requiresBearer: false, ct);

    [AllowAnonymous]
    [HttpPost("refresh")]
    public Task<IActionResult> Refresh([FromBody] JsonElement request, CancellationToken ct) =>
        ForwardAuthAsync(HttpMethod.Post, "api/account/refresh", request, requiresBearer: false, ct);

    [Authorize]
    [HttpPost("logout")]
    public Task<IActionResult> Logout([FromBody] JsonElement request, CancellationToken ct) =>
        ForwardAuthAsync(HttpMethod.Post, "api/account/logout", request, requiresBearer: true, ct);

    /// <summary>Returns the current user's profile.</summary>
    [Authorize]
    [HttpGet("me")]
    public async Task<IActionResult> Me(CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();

        var result = await _account.GetCurrentUserAsync(token, ct);
        return result.IsSuccess
            ? Ok(result.Value)
            : StatusCode(503, result.Error);
    }

    private async Task<IActionResult> ForwardAuthAsync(
        HttpMethod method,
        string path,
        JsonElement request,
        bool requiresBearer,
        CancellationToken ct)
    {
        var bearerToken = requiresBearer ? GetRawToken() : null;
        if (requiresBearer && bearerToken is null)
        {
            return Unauthorized(ErrorResponse.Unauthorized(HttpContext.GetCorrelationId()));
        }

        try
        {
            var response = await _authProxy.ForwardAsync(method, path, request, bearerToken, ct);
            return new ContentResult
            {
                StatusCode = response.StatusCode,
                Content = response.Content,
                ContentType = response.ContentType,
            };
        }
        catch
        {
            return StatusCode(503, ErrorResponse.ServiceUnavailable("account", HttpContext.GetCorrelationId()));
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
