using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using NotificationService.Application.DTOs;
using NotificationService.Application.Services;

namespace NotificationService.API.Controllers;

/// <summary>
/// Durable home for user price alerts. The gateway forwards <c>/api/alerts</c> CRUD
/// here; the alert id on the wire is the Guid in "N" form (32-char hex), matching the
/// gateway's previous in-memory contract. Everything is scoped to the caller's user id.
/// </summary>
[ApiController]
[Route("api/alerts")]
[Authorize]
public sealed class AlertsController : ControllerBase
{
    private readonly IPriceAlertsAppService _svc;

    public AlertsController(IPriceAlertsAppService svc) => _svc = svc;

    [HttpGet]
    public async Task<ActionResult<IReadOnlyList<AlertResponse>>> List(CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        var alerts = await _svc.ListAsync(userId.Value, ct);
        return Ok(alerts);
    }

    [HttpPost]
    public async Task<ActionResult<AlertResponse>> Create([FromBody] CreateAlertRequest req, CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        var alert = await _svc.CreateAsync(userId.Value, req, ct);
        return Ok(alert);
    }

    [HttpPatch("{id}")]
    public async Task<ActionResult<AlertResponse>> Update(string id, [FromBody] UpdateAlertRequest req, CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        if (!Guid.TryParse(id, out var alertId)) return NotFound();

        var alert = await _svc.UpdateAsync(userId.Value, alertId, req, ct);
        return alert is null ? NotFound() : Ok(alert);
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(string id, CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        if (!Guid.TryParse(id, out var alertId)) return NotFound();

        var deleted = await _svc.DeleteAsync(userId.Value, alertId, ct);
        return deleted ? NoContent() : NotFound();
    }

    private Guid? GetUserId()
    {
        var claim = User.FindFirstValue(ClaimTypes.NameIdentifier)
                 ?? User.FindFirstValue("sub")
                 ?? User.FindFirstValue("nameid");
        return Guid.TryParse(claim, out var g) ? g : null;
    }
}
