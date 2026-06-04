using System.Text.Json;
using SocialService.Application.Common.Exceptions;

namespace SocialService.API.Middleware;

public sealed class GlobalExceptionMiddleware
{
    private readonly RequestDelegate _next;
    private readonly ILogger<GlobalExceptionMiddleware> _log;

    public GlobalExceptionMiddleware(RequestDelegate next, ILogger<GlobalExceptionMiddleware> log)
    {
        _next = next;
        _log = log;
    }

    public async Task InvokeAsync(HttpContext ctx)
    {
        try
        {
            await _next(ctx);
        }
        catch (SocialException ex)
        {
            ctx.Response.StatusCode = ex.StatusCode;
            ctx.Response.ContentType = "application/json";
            var body = JsonSerializer.Serialize(new { error = ex.Message });
            await ctx.Response.WriteAsync(body);
        }
        catch (ArgumentException ex)
        {
            ctx.Response.StatusCode = 400;
            ctx.Response.ContentType = "application/json";
            var body = JsonSerializer.Serialize(new { error = ex.Message });
            await ctx.Response.WriteAsync(body);
        }
        catch (UnauthorizedAccessException)
        {
            // Controllers throw this when a request has no resolvable user-id
            // claim. That is a client auth problem (401), not an internal 500.
            ctx.Response.StatusCode = 401;
            ctx.Response.ContentType = "application/json";
            var body = JsonSerializer.Serialize(new { error = "unauthorized" });
            await ctx.Response.WriteAsync(body);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Unhandled error");
            ctx.Response.StatusCode = 500;
            ctx.Response.ContentType = "application/json";
            var body = JsonSerializer.Serialize(new { error = "Internal server error" });
            await ctx.Response.WriteAsync(body);
        }
    }
}
