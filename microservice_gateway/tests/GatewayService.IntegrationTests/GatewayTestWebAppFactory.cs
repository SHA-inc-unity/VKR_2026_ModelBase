using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace GatewayService.IntegrationTests;

/// <summary>
/// Test web application factory that replaces real downstream clients with in-memory fakes.
/// </summary>
public sealed class GatewayTestWebAppFactory : WebApplicationFactory<Program>
{
    public static readonly AccountUserDto TestUser = new()
    {
        Id = Guid.Parse("11111111-0000-0000-0000-000000000001"),
        Email = "test@example.com",
        Username = "testuser",
        Status = "active",
        Roles = ["user"],
        CreatedAt = DateTimeOffset.UtcNow
    };

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Test");

        // Provide a valid JWT secret key so the SymmetricSecurityKey constructor
        // does not throw IDX10703 (key length zero) during test runs.
        builder.ConfigureAppConfiguration((_, config) =>
            config.AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Jwt:SecretKey"] = "test-only-secret-key-minimum-32-chars-!!!"
            }));

        builder.ConfigureServices(services =>
        {
            // Replace the real Account service client with an in-memory fake.
            services.RemoveAll<IAccountServiceClient>();
            services.AddSingleton<IAccountServiceClient>(new FakeAccountServiceClient(TestUser));
        });
    }
}

/// <summary>Always returns the provided <see cref="AccountUserDto"/> for any token.</summary>
internal sealed class FakeAccountServiceClient : IAccountServiceClient
{
    private readonly AccountUserDto _user;
    public FakeAccountServiceClient(AccountUserDto user) => _user = user;

    public Task<ServiceResult<AccountUserDto>> GetCurrentUserAsync(string bearerToken, CancellationToken ct = default) =>
        Task.FromResult(ServiceResult<AccountUserDto>.Ok(_user));
}
