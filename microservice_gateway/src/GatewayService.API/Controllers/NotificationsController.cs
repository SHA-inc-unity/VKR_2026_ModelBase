using System.Text.Json;
using GatewayService.API.Clients.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// User notifications proxy — forwards to microservice_notification.
/// </summary>
[ApiController]
[Route("api/notifications")]
public sealed class NotificationsController : ControllerBase
{
    private readonly INotificationsHttpProxyClient _proxy;

    public NotificationsController(INotificationsHttpProxyClient proxy) => _proxy = proxy;

    [Authorize]
    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();
        var query = Request.QueryString.HasValue ? Request.QueryString.Value!.TrimStart('?') : null;
        var resp = await _proxy.ForwardAsync(HttpMethod.Get, "api/notifications", query, bearerToken: token, ct: ct);
        return new ContentResult { StatusCode = resp.StatusCode, Content = resp.Content, ContentType = resp.ContentType };
    }

    [Authorize]
    [HttpGet("unread-count")]
    public async Task<IActionResult> Unread(CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();
        var resp = await _proxy.ForwardAsync(HttpMethod.Get, "api/notifications/unread-count", bearerToken: token, ct: ct);
        return new ContentResult { StatusCode = resp.StatusCode, Content = resp.Content, ContentType = resp.ContentType };
    }

    [Authorize]
    [HttpPost("{id:guid}/read")]
    public async Task<IActionResult> MarkRead(Guid id, CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();
        var resp = await _proxy.ForwardAsync(HttpMethod.Post, $"api/notifications/{id}/read", bearerToken: token, ct: ct);
        return new ContentResult { StatusCode = resp.StatusCode, Content = resp.Content, ContentType = resp.ContentType };
    }

    [Authorize]
    [HttpPost("read-all")]
    public async Task<IActionResult> MarkAllRead(CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();
        var resp = await _proxy.ForwardAsync(HttpMethod.Post, "api/notifications/read-all", bearerToken: token, ct: ct);
        return new ContentResult { StatusCode = resp.StatusCode, Content = resp.Content, ContentType = resp.ContentType };
    }

    /// <summary>
    /// SSE bridge. Accepts the bearer token via Authorization header or via ?access_token= query param
    /// (EventSource in browsers cannot set headers).
    /// </summary>
    [AllowAnonymous]
    [HttpGet("stream")]
    public async Task Stream([FromQuery(Name = "access_token")] string? accessToken, CancellationToken ct)
    {
        var token = GetRawToken() ?? accessToken;
        if (string.IsNullOrWhiteSpace(token))
        {
            Response.StatusCode = StatusCodes.Status401Unauthorized;
            return;
        }

        var upstreamPath = string.IsNullOrEmpty(accessToken)
            ? "api/notifications/stream"
            : $"api/notifications/stream?access_token={Uri.EscapeDataString(accessToken)}";

        using var upstream = await _proxy.OpenStreamAsync(upstreamPath, token, ct);
        Response.StatusCode = (int)upstream.StatusCode;
        Response.Headers["Content-Type"] = "text/event-stream";
        Response.Headers["Cache-Control"] = "no-cache";
        Response.Headers["Connection"] = "keep-alive";
        Response.Headers["X-Accel-Buffering"] = "no";

        await using var src = await upstream.Content.ReadAsStreamAsync(ct);
        var buf = new byte[4096];
        try
        {
            while (!ct.IsCancellationRequested)
            {
                var read = await src.ReadAsync(buf, ct);
                if (read <= 0) break;
                await Response.Body.WriteAsync(buf.AsMemory(0, read), ct);
                await Response.Body.FlushAsync(ct);
            }
        }
        catch
        {
            // Client disconnected — that's fine.
        }
    }

    [Authorize]
    [HttpGet("/api/notification-settings")]
    public async Task<IActionResult> GetSettings(CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();
        var resp = await _proxy.ForwardAsync(HttpMethod.Get, "api/notification-settings", bearerToken: token, ct: ct);
        return new ContentResult { StatusCode = resp.StatusCode, Content = resp.Content, ContentType = resp.ContentType };
    }

    [Authorize]
    [HttpPut("/api/notification-settings")]
    public async Task<IActionResult> UpdateSettings([FromBody] JsonElement body, CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();
        var resp = await _proxy.ForwardAsync(HttpMethod.Put, "api/notification-settings", body: body, bearerToken: token, ct: ct);
        return new ContentResult { StatusCode = resp.StatusCode, Content = resp.Content, ContentType = resp.ContentType };
    }

    private string? GetRawToken()
    {
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is null || !header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;
        return header["Bearer ".Length..].Trim();
    }
}
