using System.Security.Claims;
using GatewayService.API.Aggregators.Dashboard;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Returns the aggregated main-screen dashboard.
/// Guests get public market/news sections only; authenticated users also get personal sections.
/// </summary>
[ApiController]
[Route("api/dashboard")]
[AllowAnonymous]
public sealed class DashboardController : ControllerBase
{
    private readonly IDashboardAggregator _aggregator;

    public DashboardController(IDashboardAggregator aggregator) => _aggregator = aggregator;

    /// <summary>Aggregate portfolio, market overview, trending, and latest news in one shot.</summary>
    [HttpGet]
    public async Task<IActionResult> Get(CancellationToken ct)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue("sub")
                  ?? string.Empty;

        var response = await _aggregator.AggregateAsync(
            string.IsNullOrWhiteSpace(userId) ? null : userId,
            ct);
        return Ok(response);
    }
}
