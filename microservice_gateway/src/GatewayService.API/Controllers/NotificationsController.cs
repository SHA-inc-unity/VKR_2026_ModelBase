using System.Security.Claims;
using GatewayService.API.Clients.Notifications;
using GatewayService.API.DTOs.Responses;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// User notifications — requires authentication.
/// </summary>
[ApiController]
[Route("api/notifications")]
[Authorize]
public sealed class NotificationsController : ControllerBase
{
    private readonly INotificationsServiceClient _notifications;

    public NotificationsController(INotificationsServiceClient notifications) =>
        _notifications = notifications;

    /// <summary>Returns the current user's notifications.</summary>
    /// <param name="limit">Max items to return (default 50).</param>
    [HttpGet]
    public async Task<IActionResult> GetList([FromQuery] int limit = 50, CancellationToken ct = default)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
                  ?? User.FindFirstValue("sub")
                  ?? string.Empty;

        limit = Math.Clamp(limit, 1, 100);
        var result = await _notifications.GetForUserAsync(userId, limit, ct);

        var items = result.IsSuccess ? result.Value ?? [] : [];
        var response = new NotificationsResponse
        {
            Items = items,
            UnreadCount = items.Count(n => !n.IsRead),
            Degraded = !result.IsSuccess
        };

        return Ok(response);
    }
}
