using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using FluentAssertions;
using GatewayService.API.Middleware;
using Xunit;

namespace GatewayService.IntegrationTests;

public sealed class GatewayIntegrationTests : IClassFixture<GatewayTestWebAppFactory>
{
    private readonly HttpClient _client;

    public GatewayIntegrationTests(GatewayTestWebAppFactory factory)
    {
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task Health_check_returns_200()
    {
        var response = await _client.GetAsync("/health");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task Bootstrap_anonymous_returns_200_with_null_user()
    {
        var response = await _client.GetAsync("/api/app/bootstrap");

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var json = await response.Content.ReadAsStringAsync();
        var doc = JsonDocument.Parse(json);
        doc.RootElement.GetProperty("user").ValueKind.Should().Be(JsonValueKind.Null);
    }

    [Fact]
    public async Task Bootstrap_with_any_bearer_returns_test_user()
    {
        // Fake client accepts any token
        _client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", "any-valid-token");
        var response = await _client.GetAsync("/api/app/bootstrap");
        _client.DefaultRequestHeaders.Authorization = null;

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var json = await response.Content.ReadAsStringAsync();
        var doc = JsonDocument.Parse(json);
        doc.RootElement.GetProperty("user").GetProperty("email").GetString()
            .Should().Be("test@example.com");
    }

    [Fact]
    public async Task Account_me_without_auth_returns_401()
    {
        var response = await _client.GetAsync("/api/account/me");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Dashboard_without_auth_returns_401()
    {
        var response = await _client.GetAsync("/api/dashboard");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task All_responses_contain_correlation_id_header()
    {
        var response = await _client.GetAsync("/api/app/bootstrap");
        response.Headers.Should().ContainKey(CorrelationIdMiddleware.HeaderName);
    }
}
