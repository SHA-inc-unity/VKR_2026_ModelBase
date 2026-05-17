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
        _token = opts.Value.SharedToken;
        _log   = log;
    }

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        if (string.IsNullOrEmpty(_token))
        {
            _log.LogWarning(
                "AdminApiKeyFilter: Admin:SharedToken is empty — allowing unauthenticated access. " +
                "Set a shared token in production.");
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

        if (string.IsNullOrEmpty(provided) || provided != _token)
        {
            context.Result = new ObjectResult(new { error = "unauthorized" })
            {
                StatusCode = StatusCodes.Status401Unauthorized,
            };
            return;
        }

        await next();
    }
}
