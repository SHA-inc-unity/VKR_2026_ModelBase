using System.Text.Json;
using GatewayService.API.Clients.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Price alerts proxy — forwards <c>/api/alerts</c> CRUD to microservice_notification,
/// which is now the durable source of truth and runs the alert evaluator.
/// The public contract is identical to the previous gateway-local implementation,
/// so the Flutter alerts screen needs no change. The notification service re-derives
/// the userId from the forwarded bearer token; the gateway no longer stores alerts.
/// </summary>
[ApiController]
[Route("api/alerts")]
[Authorize]
public sealed class AlertsController : ControllerBase
{
    private readonly INotificationsHttpProxyClient _proxy;

    public AlertsController(INotificationsHttpProxyClient proxy) => _proxy = proxy;

    [HttpGet]
    public Task<IActionResult> GetAlerts(CancellationToken ct) =>
        Forward(HttpMethod.Get, "api/alerts", ct: ct);

    [HttpPost]
    public Task<IActionResult> Create([FromBody] JsonElement body, CancellationToken ct) =>
        Forward(HttpMethod.Post, "api/alerts", body: body, ct: ct);

    [HttpPatch("{id}")]
    public Task<IActionResult> Update(string id, [FromBody] JsonElement body, CancellationToken ct) =>
        Forward(HttpMethod.Patch, $"api/alerts/{Uri.EscapeDataString(id)}", body: body, ct: ct);

    [HttpDelete("{id}")]
    public Task<IActionResult> Delete(string id, CancellationToken ct) =>
        Forward(HttpMethod.Delete, $"api/alerts/{Uri.EscapeDataString(id)}", ct: ct);

    private async Task<IActionResult> Forward(
        HttpMethod method,
        string path,
        JsonElement? body = null,
        CancellationToken ct = default)
    {
        var token = GetRawToken();
        if (token is null) return Unauthorized();

        var query = Request.QueryString.HasValue ? Request.QueryString.Value!.TrimStart('?') : null;

        try
        {
            var resp = await _proxy.ForwardAsync(method, path, query, body, token, ct);
            return new ContentResult
            {
                StatusCode = resp.StatusCode,
                Content = resp.Content,
                ContentType = resp.ContentType,
            };
        }
        catch
        {
            return StatusCode(503, new { error = "notification_service_unavailable" });
        }
    }

    private string? GetRawToken()
    {
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is null || !header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;
        return header["Bearer ".Length..].Trim();
    }
}
