using System.Security.Claims;
using GatewayService.API.Aggregators.Dashboard;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Returns the aggregated main-screen dashboard.
/// </summary>
[ApiController]
[Route("api/dashboard")]
[Authorize]
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

        var response = await _aggregator.AggregateAsync(userId, ct);
        return Ok(response);
    }
}
