using GatewayService.API.Updates;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Public app-updates / changelog endpoint. Fetches the releases list from
/// microservice_data over Kafka and passes it through verbatim. Publicly
/// accessible (no auth).
/// </summary>
[ApiController]
[Route("api/updates")]
[AllowAnonymous]
public sealed class UpdatesController : ControllerBase
{
    private readonly IUpdatesService _updates;

    public UpdatesController(IUpdatesService updates) => _updates = updates;

    [HttpGet]
    public async Task<IActionResult> GetUpdates(CancellationToken ct)
    {
        var result = await _updates.GetUpdatesAsync(ct);
        if (!result.Ok || result.Json is null)
        {
            return StatusCode(503, new { error = "updates_unavailable" });
        }

        Response.Headers["Cache-Control"] = "public, max-age=120, stale-while-revalidate=600";
        return Content(result.Json, "application/json");
    }
}
