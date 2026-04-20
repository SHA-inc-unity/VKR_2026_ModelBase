namespace GatewayService.API.Middleware;

/// <summary>
/// Ensures every request has a correlation ID, either passed in by the caller or generated fresh.
/// Stored in HttpContext.Items and echoed back in X-Correlation-Id response header.
/// </summary>
public sealed class CorrelationIdMiddleware
{
    public const string HeaderName = "X-Correlation-Id";
    public const string ItemsKey = "CorrelationId";

    private readonly RequestDelegate _next;

    public CorrelationIdMiddleware(RequestDelegate next) => _next = next;

    public async Task InvokeAsync(HttpContext context)
    {
        var correlationId = context.Request.Headers[HeaderName].FirstOrDefault();
        if (string.IsNullOrWhiteSpace(correlationId))
            correlationId = Guid.NewGuid().ToString("N");

        context.Items[ItemsKey] = correlationId;
        context.Response.Headers.TryAdd(HeaderName, correlationId);

        await _next(context);
    }
}

public static class HttpContextExtensions
{
    public static string? GetCorrelationId(this HttpContext context) =>
        context.Items.TryGetValue(CorrelationIdMiddleware.ItemsKey, out var v) ? v as string : null;
}
