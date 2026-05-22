using GatewayService.API.Clients.Account;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Settings;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Aggregators.Bootstrap;

public sealed class BootstrapAggregator : IBootstrapAggregator
{
    private readonly IAccountServiceClient _account;
    private readonly IOptions<FeatureFlagsSettings> _flags;
    private readonly ILogger<BootstrapAggregator> _logger;

    public BootstrapAggregator(
        IAccountServiceClient account,
        IOptions<FeatureFlagsSettings> flags,
        ILogger<BootstrapAggregator> logger)
    {
        _account = account;
        _flags = flags;
        _logger = logger;
    }

    public async Task<BootstrapResponse> AggregateAsync(string? bearerToken, CancellationToken ct = default)
    {
        var degraded = new List<string>();
        UserSummaryDto? userSummary = null;

        if (!string.IsNullOrEmpty(bearerToken))
        {
            var userResult = await _account.GetCurrentUserAsync(bearerToken, ct);
            if (userResult.IsSuccess && userResult.Value is { } user)
            {
                userSummary = new UserSummaryDto
                {
                    Id = user.Id,
                    Email = user.Email,
                    Username = user.Username,
                    Status = user.Status,
                    Roles = user.Roles,
                    CreatedAt = user.CreatedAt
                };
            }
            else
            {
                _logger.LogWarning("Account service degraded during bootstrap: {Error}", userResult.Error);
                degraded.Add("account");
            }
        }

        var flags = _flags.Value;
        var services = BuildServiceStatus(degraded);

        return new BootstrapResponse
        {
            User = userSummary,
            FeatureFlags = new FeatureFlagsDto
            {
                Portfolio = flags.Portfolio,
                Market = flags.Market,
                News = flags.News,
                Notifications = flags.Notifications
            },
            SystemStatus = new SystemStatusDto
            {
                Status = degraded.Count == 0 ? "operational" : "degraded",
                Services = services
            },
            ApiVersion = "1.0",
            GeneratedAt = DateTimeOffset.UtcNow,
            DegradedServices = degraded
        };
    }

    private static IReadOnlyDictionary<string, string> BuildServiceStatus(IReadOnlyList<string> degraded)
    {
        var services = new[] { "account" };
        return services.ToDictionary(
            s => s,
            s => degraded.Contains(s) ? "degraded" : "operational");
    }
}
