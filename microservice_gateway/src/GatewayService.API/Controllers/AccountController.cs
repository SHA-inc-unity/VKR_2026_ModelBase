using GatewayService.API.Clients.Account;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Proxies account/profile requests to the Account Service.
/// </summary>
[ApiController]
[Route("api/account")]
[Authorize]
public sealed class AccountController : ControllerBase
{
    private readonly IAccountServiceClient _account;

    public AccountController(IAccountServiceClient account) => _account = account;

    /// <summary>Returns the current user's profile.</summary>
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

    private string? GetRawToken()
    {
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is null || !header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;
        return header["Bearer ".Length..].Trim();
    }
}
