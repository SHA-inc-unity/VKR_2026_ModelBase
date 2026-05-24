using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using NotificationService.API.Services;
using NotificationService.API.Sse;
using NotificationService.Application.DTOs;
using NotificationService.Application.Services;

namespace NotificationService.API.Controllers;

[ApiController]
[Route("api/notifications")]
[Authorize]
public sealed class NotificationsController : ControllerBase
{
    private readonly INotificationsAppService _svc;
    private readonly SseDispatcher _sse;

    public NotificationsController(INotificationsAppService svc, SseDispatcher sse)
    {
        _svc = svc;
        _sse = sse;
    }

    [HttpGet]
    public async Task<ActionResult<NotificationListResponse>> List(
        [FromQuery] bool unreadOnly = false,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 50,
        CancellationToken ct = default)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        var res = await _svc.ListAsync(userId.Value, unreadOnly, page, pageSize, ct);
        return Ok(res);
    }

    [HttpGet("unread-count")]
    public async Task<ActionResult<UnreadCountResponse>> Unread(CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        var unread = await _svc.GetUnreadCountAsync(userId.Value, ct);
        return Ok(new UnreadCountResponse { Unread = unread });
    }

    [HttpPost("{id:guid}/read")]
    public async Task<IActionResult> MarkRead(Guid id, CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        await _svc.MarkReadAsync(id, userId.Value, ct);
        return NoContent();
    }

    [HttpPost("read-all")]
    public async Task<IActionResult> MarkAllRead(CancellationToken ct)
    {
        var userId = GetUserId();
        if (userId is null) return Unauthorized();
        await _svc.MarkAllReadAsync(userId.Value, ct);
        return NoContent();
    }

    [HttpGet("stream")]
    [AllowAnonymous] // We validate the token manually here because EventSource cannot set headers.
    public async Task Stream([FromQuery(Name = "access_token")] string? accessToken, CancellationToken ct)
    {
        Guid? userId = GetUserId();
        if (userId is null && !string.IsNullOrWhiteSpace(accessToken))
        {
            // The bearer middleware already ran with the header; for the query-token path we delegate to
            // the JwtBearer handler — this is the simplest approach: rebuild a ClaimsPrincipal from the token.
            var handler = HttpContext.RequestServices.GetRequiredService<IJwtTokenValidator>();
            userId = handler.ResolveUserId(accessToken!);
        }

        if (userId is null)
        {
            Response.StatusCode = StatusCodes.Status401Unauthorized;
            return;
        }

        Response.Headers["Content-Type"] = "text/event-stream";
        Response.Headers["Cache-Control"] = "no-cache";
        Response.Headers["Connection"] = "keep-alive";
        Response.Headers["X-Accel-Buffering"] = "no";

        var client = new SseClient
        {
            Response = Response,
            Token = ct,
            UserId = userId.Value,
        };
        _sse.Register(client);

        try
        {
            // Initial flush so the client knows the channel is alive.
            await Response.WriteAsync(": connected\n\n", ct);
            await Response.Body.FlushAsync(ct);

            while (!ct.IsCancellationRequested)
            {
                try { await Task.Delay(TimeSpan.FromSeconds(25), ct); }
                catch (OperationCanceledException) { break; }

                try
                {
                    await Response.WriteAsync(": keep-alive\n\n", ct);
                    await Response.Body.FlushAsync(ct);
                }
                catch
                {
                    break;
                }
            }
        }
        finally
        {
            _sse.Unregister(client);
        }
    }

    private Guid? GetUserId()
    {
        var claim = User.FindFirstValue(ClaimTypes.NameIdentifier)
                 ?? User.FindFirstValue("sub")
                 ?? User.FindFirstValue("nameid");
        return Guid.TryParse(claim, out var g) ? g : null;
    }
}
