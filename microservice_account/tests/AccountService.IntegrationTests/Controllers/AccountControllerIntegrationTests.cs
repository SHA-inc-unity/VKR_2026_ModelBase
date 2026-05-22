using System.Net;
using System.Net.Http.Json;
using AccountService.Application.DTOs.Requests;
using AccountService.Application.DTOs.Responses;
using AccountService.IntegrationTests.Infrastructure;
using FluentAssertions;
using Xunit;

namespace AccountService.IntegrationTests.Controllers;

public sealed class AccountControllerIntegrationTests : IClassFixture<IntegrationTestWebAppFactory>
{
    private readonly HttpClient _client;

    public AccountControllerIntegrationTests(IntegrationTestWebAppFactory factory)
    {
        _client = factory.CreateClient();
    }

    [Fact]
    public async Task Register_HappyPath_Returns200WithTokens()
    {
        var request = new RegisterRequest(
            $"user_{Guid.NewGuid():N}@test.com",
            $"user_{Guid.NewGuid():N}"[..20],
            "Password1");

        var response = await _client.PostAsJsonAsync("/api/account/register", request);

        response.StatusCode.Should().Be(HttpStatusCode.OK);
        var body = await response.Content.ReadFromJsonAsync<AuthResponse>();
        body!.AccessToken.Should().NotBeNullOrEmpty();
        body.RefreshToken.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public async Task Register_DuplicateEmail_Returns409()
    {
        var email = $"dup_{Guid.NewGuid():N}@test.com";
        var request1 = new RegisterRequest(email, $"user1_{Guid.NewGuid():N}"[..18], "Password1");
        var request2 = new RegisterRequest(email, $"user2_{Guid.NewGuid():N}"[..18], "Password1");

        await _client.PostAsJsonAsync("/api/account/register", request1);
        var response = await _client.PostAsJsonAsync("/api/account/register", request2);

        response.StatusCode.Should().Be(HttpStatusCode.Conflict);
    }

    [Fact]
    public async Task Login_ValidCredentials_Returns200()
    {
        var email = $"login_{Guid.NewGuid():N}@test.com";
        var password = "Password1";
        var username = $"ln_{Guid.NewGuid():N}"[..15];

        await _client.PostAsJsonAsync("/api/account/register",
            new RegisterRequest(email, username, password));

        var response = await _client.PostAsJsonAsync("/api/account/login",
            new LoginRequest(email, password));

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task Login_WrongPassword_Returns401()
    {
        var email = $"wp_{Guid.NewGuid():N}@test.com";
        await _client.PostAsJsonAsync("/api/account/register",
            new RegisterRequest(email, $"wp_{Guid.NewGuid():N}"[..15], "Password1"));

        var response = await _client.PostAsJsonAsync("/api/account/login",
            new LoginRequest(email, "wrongpassword"));

        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Refresh_ValidToken_Returns200()
    {
        var email = $"ref_{Guid.NewGuid():N}@test.com";
        var regResp = await _client.PostAsJsonAsync("/api/account/register",
            new RegisterRequest(email, $"rf_{Guid.NewGuid():N}"[..15], "Password1"));
        var auth = await regResp.Content.ReadFromJsonAsync<AuthResponse>();

        var response = await _client.PostAsJsonAsync("/api/account/refresh",
            new RefreshTokenRequest(auth!.RefreshToken));

        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task Me_WithoutToken_Returns401()
    {
        var response = await _client.GetAsync("/api/account/me");
        response.StatusCode.Should().Be(HttpStatusCode.Unauthorized);
    }

    [Fact]
    public async Task Me_WithValidToken_Returns200()
    {
        var email = $"me_{Guid.NewGuid():N}@test.com";
        var regResp = await _client.PostAsJsonAsync("/api/account/register",
            new RegisterRequest(email, $"me_{Guid.NewGuid():N}"[..15], "Password1"));
        var auth = await regResp.Content.ReadFromJsonAsync<AuthResponse>();

        _client.DefaultRequestHeaders.Authorization =
            new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", auth!.AccessToken);

        var response = await _client.GetAsync("/api/account/me");
        response.StatusCode.Should().Be(HttpStatusCode.OK);

        // cleanup
        _client.DefaultRequestHeaders.Authorization = null;
    }
}
