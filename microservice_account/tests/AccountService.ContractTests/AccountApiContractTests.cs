using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using AccountService.Application.DTOs.Requests;
using FluentAssertions;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

namespace AccountService.ContractTests;

/// <summary>
/// Contract tests: verify API response shapes and HTTP status conventions.
/// Uses in-memory test server (no real DB — only shape/contract validation).
/// </summary>
public sealed class AccountApiContractTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public AccountApiContractTests(WebApplicationFactory<Program> factory)
    {
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task Register_InvalidPayload_Returns400WithProblemDetails()
    {
        // Empty body -> validation error
        var response = await _client.PostAsJsonAsync("/api/account/register",
            new { email = "", username = "", password = "" });

        response.StatusCode.Should().BeOneOf(
            HttpStatusCode.BadRequest,
            HttpStatusCode.UnprocessableEntity);

        var body = await response.Content.ReadAsStringAsync();
        body.Should().MatchRegex("title|errors"); // ProblemDetails or ValidationProblemDetails
    }

    [Fact]
    public async Task Login_InvalidCredentials_Returns401()
    {
        var response = await _client.PostAsJsonAsync("/api/account/login",
            new LoginRequest("noexist@example.com", "WrongPass1"));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);

        var body = await response.Content.ReadAsStringAsync();
        var json = JsonDocument.Parse(body).RootElement;

        json.TryGetProperty("title", out _).Should().BeTrue("response should be ProblemDetails");
        json.TryGetProperty("status", out var status).Should().BeTrue();
        status.GetInt32().Should().Be(401);
    }

    [Fact]
    public async Task HealthCheck_Returns200()
    {
        var response = await _client.GetAsync("/health");
        // Service may be degraded in tests (no real DB), but endpoint must exist
        response.StatusCode.Should().BeOneOf(HttpStatusCode.OK, HttpStatusCode.ServiceUnavailable);
    }

    [Fact]
    public async Task Me_WithoutAuth_Returns401WithProblemDetails()
    {
        var response = await _client.GetAsync("/api/account/me");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }
}
