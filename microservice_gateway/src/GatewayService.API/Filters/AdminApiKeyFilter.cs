using System.Security.Cryptography;
using System.Text;
using GatewayService.API.DTOs;
using GatewayService.API.Middleware;
using GatewayService.API.Settings;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Filters;

/// <summary>
/// Action filter that enforces shared-secret authentication for all
/// /api/admin/* endpoints.
///
/// Accepts the token in either of two headers (checked in this order):
///   1. Authorization: Bearer &lt;token&gt;
///   2. X-Admin-Api-Key: &lt;token&gt;
///
/// When AdminSettings.SharedToken is empty the filter allows the request
/// through with a warning — this behaviour is intentional for local /
/// full-stack mode where the gateway and admin run on the same host.
/// In production always set a non-empty shared token.
/// </summary>
public sealed class AdminApiKeyFilter : IAsyncActionFilter
{
    private readonly string _token;
    private readonly ILogger<AdminApiKeyFilter> _log;

    public AdminApiKeyFilter(IOptions<AdminSettings> opts, ILogger<AdminApiKeyFilter> log)
    {
        _token = opts.Value.SharedToken.Trim();
        _log   = log;
    }

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        if (string.IsNullOrEmpty(_token))
        {
            _log.LogWarning(
                "AdminApiKeyFilter: code=admin_token_not_configured path={Path} correlationId={CorrelationId}. " +
                "Admin:SharedToken is empty — allowing unauthenticated access. Set a shared token in production.",
                context.HttpContext.Request.Path,
                context.HttpContext.GetCorrelationId());
            await next();
            return;
        }

        var headers = context.HttpContext.Request.Headers;
        string? provided = null;

        var authHeader = headers["Authorization"].FirstOrDefault();
        if (authHeader?.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase) == true)
            provided = authHeader["Bearer ".Length..].Trim();

        if (string.IsNullOrEmpty(provided))
            provided = headers["X-Admin-Api-Key"].FirstOrDefault()?.Trim();

        if (string.IsNullOrEmpty(provided))
        {
            var correlationId = context.HttpContext.GetCorrelationId();
            _log.LogWarning(
                "AdminApiKeyFilter: code=admin_token_missing path={Path} remoteIp={RemoteIp} correlationId={CorrelationId}",
                context.HttpContext.Request.Path,
                context.HttpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown",
                correlationId);
            context.Result = new ObjectResult(ErrorResponse.AdminUnauthorized(
                "admin_token_missing",
                "Admin shared token is missing. Send Authorization: Bearer <token> or X-Admin-Api-Key.",
                correlationId))
            {
                StatusCode = StatusCodes.Status401Unauthorized,
            };
            return;
        }

        if (!TokenEquals(provided, _token))
        {
            var correlationId = context.HttpContext.GetCorrelationId();
            _log.LogWarning(
                "AdminApiKeyFilter: code=admin_token_invalid path={Path} remoteIp={RemoteIp} correlationId={CorrelationId}",
                context.HttpContext.Request.Path,
                context.HttpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown",
                correlationId);
            context.Result = new ObjectResult(ErrorResponse.AdminUnauthorized(
                "admin_token_invalid",
                "Admin shared token was rejected by backend. ADMIN_BACKEND_SHARED_TOKEN must match ADMIN_SHARED_TOKEN on backend-host.",
                correlationId))
            {
                StatusCode = StatusCodes.Status401Unauthorized,
            };
            return;
        }

        await next();
    }

    private static bool TokenEquals(string provided, string expected)
    {
        var providedBytes = Encoding.UTF8.GetBytes(provided);
        var expectedBytes = Encoding.UTF8.GetBytes(expected);
        return providedBytes.Length == expectedBytes.Length &&
               CryptographicOperations.FixedTimeEquals(providedBytes, expectedBytes);
    }
}
