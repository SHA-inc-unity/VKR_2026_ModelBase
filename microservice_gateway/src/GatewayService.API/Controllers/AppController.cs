using GatewayService.API.Aggregators.Bootstrap;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Bootstrap endpoint — called ONCE on Flutter app launch.
/// Works unauthenticated; user section is populated if a valid JWT is provided.
/// </summary>
[ApiController]
[Route("api/app")]
public sealed class AppController : ControllerBase
{
    private readonly IBootstrapAggregator _aggregator;

    public AppController(IBootstrapAggregator aggregator) => _aggregator = aggregator;

    /// <summary>
    /// Returns user summary (if authenticated), feature flags, and system status in one call.
    /// </summary>
    [HttpGet("bootstrap")]
    [AllowAnonymous]
    public async Task<IActionResult> Bootstrap(CancellationToken ct)
    {
        var token = GetRawToken();
        var response = await _aggregator.AggregateAsync(token, ct);
        return Ok(response);
    }

    private string? GetRawToken()
    {
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is null || !header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;
        return header["Bearer ".Length..].Trim();
    }
}
