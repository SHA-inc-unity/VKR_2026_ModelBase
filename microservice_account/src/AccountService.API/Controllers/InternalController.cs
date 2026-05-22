using AccountService.Application.Interfaces.Services;
using Microsoft.AspNetCore.Mvc;

namespace AccountService.API.Controllers;

/// <summary>
/// Internal endpoints for inter-service communication.
/// Protected by API key header (X-Internal-Api-Key).
/// Not exposed via public Swagger — for service mesh only.
/// </summary>
[ApiController]
[Route("internal")]
public sealed class InternalController : ControllerBase
{
    private readonly IAccountService _accountService;
    private readonly IConfiguration _config;

    public InternalController(IAccountService accountService, IConfiguration config)
    {
        _accountService = accountService;
        _config = config;
    }

    [HttpGet("users/{id:guid}")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> GetUser(Guid id, CancellationToken ct)
    {
        if (!IsAuthorized()) return Unauthorized();
        var result = await _accountService.GetInternalUserAsync(id, ct);
        return Ok(result);
    }

    [HttpGet("users/by-email/{email}")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> GetUserByEmail(string email, CancellationToken ct)
    {
        if (!IsAuthorized()) return Unauthorized();
        var result = await _accountService.GetInternalUserByEmailAsync(email, ct);
        return result is null ? NotFound() : Ok(result);
    }

    [HttpGet("users/{id:guid}/roles")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> GetUserRoles(Guid id, CancellationToken ct)
    {
        if (!IsAuthorized()) return Unauthorized();
        var user = await _accountService.GetInternalUserAsync(id, ct);
        return Ok(user.Roles);
    }

    private bool IsAuthorized()
    {
        var expected = _config["InternalApi:ApiKey"];
        if (string.IsNullOrWhiteSpace(expected)) return false;
        Request.Headers.TryGetValue("X-Internal-Api-Key", out var provided);
        return provided == expected;
    }
}
