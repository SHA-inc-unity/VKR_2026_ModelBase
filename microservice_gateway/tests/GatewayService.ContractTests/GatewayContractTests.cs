using System.Net;
using System.Text.Json;
using FluentAssertions;
using GatewayService.IntegrationTests;
using GatewayService.API.Middleware;
using Xunit;

namespace GatewayService.ContractTests;

/// <summary>
/// Verifies the JSON shape of each API contract (not business logic).
/// </summary>
public sealed class GatewayContractTests : IClassFixture<GatewayTestWebAppFactory>
{
    private readonly HttpClient _client;

    public GatewayContractTests(GatewayTestWebAppFactory factory) => _client = factory.CreateClient();

    [Fact]
    public async Task Bootstrap_contract_has_required_fields()
    {
        var response = await _client.GetAsync("/api/app/bootstrap");
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        var root = doc.RootElement;

        // Required top-level fields
        root.TryGetProperty("user", out _).Should().BeTrue("user field must be present (null allowed)");
        root.TryGetProperty("featureFlags", out _).Should().BeTrue();
        root.TryGetProperty("degradedServices", out _).Should().BeTrue();
        root.TryGetProperty("apiVersion", out var version).Should().BeTrue();
        root.TryGetProperty("generatedAt", out _).Should().BeTrue();
        root.TryGetProperty("systemStatus", out _).Should().BeTrue();

        version.GetString().Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task Unauthorized_endpoint_returns_error_response_shape()
    {
        var response = await _client.GetAsync("/api/account/me");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
        response.Content.Headers.ContentType?.MediaType.Should().Contain("application/json");

        var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        var root = doc.RootElement;
        root.TryGetProperty("status", out _).Should().BeTrue();
        root.TryGetProperty("title", out _).Should().BeTrue();
        root.TryGetProperty("timestamp", out _).Should().BeTrue();
    }

    [Fact]
    public async Task Health_endpoint_responds()
    {
        var response = await _client.GetAsync("/health");
        ((int)response.StatusCode).Should().BeOneOf(200, 503);
    }

    [Fact]
    public async Task Response_always_includes_correlation_id()
    {
        var response = await _client.GetAsync("/api/app/bootstrap");
        response.Headers.TryGetValues(CorrelationIdMiddleware.HeaderName, out var values)
            .Should().BeTrue();
        values!.First().Should().NotBeNullOrEmpty();
    }
}
