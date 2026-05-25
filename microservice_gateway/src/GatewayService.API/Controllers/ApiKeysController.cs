using System.Text.Json;
using GatewayService.API.Clients.Account;
using GatewayService.API.DTOs;
using GatewayService.API.Middleware;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

/// <summary>
/// Thin proxy to microservice_account's ApiKeysController. The gateway is
/// the public surface — it forwards the user's bearer token so the
/// downstream service still scopes the request to the right user.
/// </summary>
[ApiController]
[Route("api/account/api-keys")]
[Authorize]
public sealed class ApiKeysController : ControllerBase
{
    private readonly IAccountAuthProxyClient _authProxy;

    public ApiKeysController(IAccountAuthProxyClient authProxy) => _authProxy = authProxy;

    [HttpGet]
    public Task<IActionResult> List(CancellationToken ct) =>
        ForwardAsync(HttpMethod.Get, "api/account/api-keys", body: null, ct);

    [HttpPost]
    public Task<IActionResult> Create([FromBody] JsonElement body, CancellationToken ct) =>
        ForwardAsync(HttpMethod.Post, "api/account/api-keys", body, ct);

    [HttpDelete("{id:guid}")]
    public Task<IActionResult> Revoke(Guid id, CancellationToken ct) =>
        ForwardAsync(HttpMethod.Delete, $"api/account/api-keys/{id}", body: null, ct);

    private async Task<IActionResult> ForwardAsync(
        HttpMethod method,
        string path,
        JsonElement? body,
        CancellationToken ct)
    {
        var token = GetRawToken();
        if (token is null)
            return Unauthorized(ErrorResponse.Unauthorized(HttpContext.GetCorrelationId()));

        try
        {
            var response = await _authProxy.ForwardAsync(method, path, body, token, ct);
            if (response.StatusCode >= 400)
            {
                return StatusCode(response.StatusCode, new ErrorResponse
                {
                    Status = response.StatusCode,
                    Title = "API Key Error",
                    Code = "api_keys_proxy_error",
                    Detail = ExtractError(response.Content),
                    CorrelationId = HttpContext.GetCorrelationId(),
                });
            }
            return new ContentResult
            {
                StatusCode = response.StatusCode,
                Content = response.Content,
                ContentType = response.ContentType,
            };
        }
        catch
        {
            return StatusCode(503, ErrorResponse.ServiceUnavailable("account", HttpContext.GetCorrelationId()));
        }
    }

    private string? GetRawToken()
    {
        var header = Request.Headers.Authorization.FirstOrDefault();
        if (header is null || !header.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return null;
        return header["Bearer ".Length..].Trim();
    }

    private static string ExtractError(string? content)
    {
        if (string.IsNullOrWhiteSpace(content)) return "API key request failed.";
        try
        {
            using var doc = JsonDocument.Parse(content);
            var root = doc.RootElement;
            if (root.ValueKind == JsonValueKind.Object &&
                root.TryGetProperty("error", out var err) && err.ValueKind == JsonValueKind.String)
                return err.GetString() ?? "API key request failed.";
        }
        catch { /* fall through */ }
        return content;
    }
}
