using System.Security.Claims;
using AccountService.Application.DTOs.Requests;
using AccountService.Application.Interfaces.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace AccountService.API.Controllers;

/// <summary>
/// Public CRUD for the user's encrypted third-party exchange API keys.
/// All endpoints require a valid Bearer JWT and act only on the caller's
/// own keys. The cleartext API key/secret are NEVER returned here — only
/// a masked preview.
/// </summary>
[ApiController]
[Route("api/account/api-keys")]
[Authorize]
public sealed class ApiKeysController : ControllerBase
{
    private readonly IExchangeApiKeyService _service;

    public ApiKeysController(IExchangeApiKeyService service) => _service = service;

    [HttpGet]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public async Task<IActionResult> List(CancellationToken ct)
    {
        var items = await _service.ListAsync(GetUserId(), ct);
        return Ok(items);
    }

    [HttpPost]
    [ProducesResponseType(StatusCodes.Status201Created)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> Create([FromBody] CreateApiKeyRequest request, CancellationToken ct)
    {
        try
        {
            var dto = await _service.CreateAsync(GetUserId(), request, ct);
            return Created($"/api/account/api-keys/{dto.Id}", dto);
        }
        catch (ArgumentException ex)
        {
            return BadRequest(new { error = ex.Message });
        }
    }

    [HttpDelete("{id:guid}")]
    [ProducesResponseType(StatusCodes.Status204NoContent)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> Revoke(Guid id, CancellationToken ct)
    {
        var ok = await _service.RevokeAsync(GetUserId(), id, ct);
        return ok ? NoContent() : NotFound();
    }

    private Guid GetUserId()
    {
        var sub = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? User.FindFirstValue("sub");
        return Guid.TryParse(sub, out var id)
            ? id
            : throw new UnauthorizedAccessException("Invalid token subject claim.");
    }
}

/// <summary>
/// Inter-service endpoint used only by the gateway to fetch a decrypted
/// API key/secret pair for outbound exchange calls. Protected by the
/// shared header <c>X-Internal-Api-Key</c> (same as InternalController).
/// </summary>
[ApiController]
[Route("internal/api-keys")]
public sealed class InternalApiKeysController : ControllerBase
{
    private readonly IExchangeApiKeyService _service;
    private readonly IConfiguration _config;

    public InternalApiKeysController(IExchangeApiKeyService service, IConfiguration config)
    {
        _service = service;
        _config = config;
    }

    [HttpGet("{userId:guid}")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status401Unauthorized)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> GetDecrypted(Guid userId, [FromQuery] string exchange = "bybit", CancellationToken ct = default)
    {
        if (!IsAuthorized()) return Unauthorized();
        var dto = await _service.GetDecryptedActiveAsync(userId, exchange, ct);
        return dto is null ? NotFound() : Ok(dto);
    }

    private bool IsAuthorized()
    {
        var expected = _config["InternalApi:ApiKey"];
        if (string.IsNullOrWhiteSpace(expected)) return false;
        Request.Headers.TryGetValue("X-Internal-Api-Key", out var provided);
        return provided == expected;
    }
}
