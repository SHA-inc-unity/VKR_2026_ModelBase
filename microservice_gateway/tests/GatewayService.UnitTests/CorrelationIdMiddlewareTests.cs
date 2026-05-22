using FluentAssertions;
using GatewayService.API.Middleware;
using Microsoft.AspNetCore.Http;
using Xunit;

namespace GatewayService.UnitTests;

public sealed class CorrelationIdMiddlewareTests
{
    [Fact]
    public async Task No_header_generates_new_correlation_id()
    {
        var ctx = new DefaultHttpContext();
        ctx.Response.Body = new System.IO.MemoryStream();

        var middleware = new CorrelationIdMiddleware(_ => Task.CompletedTask);
        await middleware.InvokeAsync(ctx);

        ctx.Items[CorrelationIdMiddleware.ItemsKey].Should().NotBeNull().And.BeOfType<string>();
        ctx.Response.Headers[CorrelationIdMiddleware.HeaderName].ToString().Should().NotBeEmpty();
    }

    [Fact]
    public async Task Existing_header_is_preserved()
    {
        const string existingId = "my-correlation-id-123";
        var ctx = new DefaultHttpContext();
        ctx.Request.Headers[CorrelationIdMiddleware.HeaderName] = existingId;
        ctx.Response.Body = new System.IO.MemoryStream();

        var middleware = new CorrelationIdMiddleware(_ => Task.CompletedTask);
        await middleware.InvokeAsync(ctx);

        ctx.Items[CorrelationIdMiddleware.ItemsKey].Should().Be(existingId);
        ctx.Response.Headers[CorrelationIdMiddleware.HeaderName].ToString().Should().Be(existingId);
    }

    [Fact]
    public async Task Extension_method_retrieves_stored_id()
    {
        const string existingId = "ext-method-test";
        var ctx = new DefaultHttpContext();
        ctx.Request.Headers[CorrelationIdMiddleware.HeaderName] = existingId;
        ctx.Response.Body = new System.IO.MemoryStream();

        var middleware = new CorrelationIdMiddleware(_ => Task.CompletedTask);
        await middleware.InvokeAsync(ctx);

        ctx.GetCorrelationId().Should().Be(existingId);
    }
}
