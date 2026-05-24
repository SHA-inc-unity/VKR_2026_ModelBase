using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using NotificationService.Application.DTOs;
using NotificationService.Application.Services;

namespace NotificationService.API.Controllers;

[ApiController]
[Route("api/notification-settings")]
[Authorize]
public sealed class NotificationSettingsController : ControllerBase
{
    private readonly INotificationsAppService _svc;
    public NotificationSettingsController(INotificationsAppService svc) => _svc = svc;

    [HttpGet]
    public async Task<ActionResult<NotificationSettingsResponse>> Get(CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        var res = await _svc.GetSettingsAsync(userId.Value, ct);
        return Ok(res);
    }

    [HttpPut]
    public async Task<ActionResult<NotificationSettingsResponse>> Update(
        [FromBody] UpdateNotificationSettingsRequest req,
        CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        var res = await _svc.UpdateSettingsAsync(userId.Value, req, ct);
        return Ok(res);
    }

    private Guid? GetUserId()
    {
        var claim = User.FindFirstValue(ClaimTypes.NameIdentifier)
                 ?? User.FindFirstValue("sub")
                 ?? User.FindFirstValue("nameid");
        return Guid.TryParse(claim, out var g) ? g : null;
    }
}
