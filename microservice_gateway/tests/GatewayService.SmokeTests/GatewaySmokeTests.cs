using System.Net;
using System.Text.Json;
using FluentAssertions;
using GatewayService.IntegrationTests;
using GatewayService.API.Middleware;
using Xunit;

namespace GatewayService.SmokeTests;

/// <summary>
/// Quick sanity checks — runs against the in-process factory (not a live server).
/// </summary>
public sealed class GatewaySmokeTests : IClassFixture<GatewayTestWebAppFactory>
{
    private readonly HttpClient _client;

    public GatewaySmokeTests(GatewayTestWebAppFactory factory) => _client = factory.CreateClient();

    [Fact]
    public async Task Health_check_is_alive()
    {
        var response = await _client.GetAsync("/health");
        ((int)response.StatusCode).Should().BeOneOf(200, 503);
    }

    [Fact]
    public async Task Bootstrap_does_not_return_5xx()
    {
        var response = await _client.GetAsync("/api/app/bootstrap");
        ((int)response.StatusCode).Should().BeLessThan(500);
    }

    [Fact]
    public async Task Unauthenticated_account_me_returns_401_with_correct_shape()
    {
        var response = await _client.GetAsync("/api/account/me");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        response.Content.Headers.ContentType?.MediaType.Should().Contain("application/json");

        var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        doc.RootElement.TryGetProperty("status", out _).Should().BeTrue();
    }

    [Fact]
    public async Task Every_response_has_correlation_id()
    {
        var response = await _client.GetAsync("/api/app/bootstrap");
        response.Headers.Contains(CorrelationIdMiddleware.HeaderName).Should().BeTrue();
    }
}
