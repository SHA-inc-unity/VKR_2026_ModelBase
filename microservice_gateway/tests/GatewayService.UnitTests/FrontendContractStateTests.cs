using FluentAssertions;
using GatewayService.API.DTOs.Requests;
using GatewayService.API.Frontend;
using GatewayService.API.Settings;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace GatewayService.UnitTests;

public sealed class FrontendContractStateTests
{
    [Fact]
    public void State_is_shared_across_instances_via_distributed_cache()
    {
        using var provider = CreateProvider();
        var cache = provider.GetRequiredService<IDistributedCache>();
        var options = CreateFlagsOptions();

        var first = new FrontendContractState(cache, options, NullLogger<FrontendContractState>.Instance);
        first.LinkExchange("user-1", new LinkExchangeRequest("binance", "abcd1234efgh5678"));
        first.CreateAlert("user-1", new CreateAlertRequest("BTCUSDT", "above", 100000m));
        first.UpdateServiceToggles(new PatchServiceTogglesRequest(Alerts: false, MarketOverview: true));

        var second = new FrontendContractState(cache, options, NullLogger<FrontendContractState>.Instance);

        second.GetLinkedExchanges("user-1").Should().ContainSingle()
            .Which.Slug.Should().Be("binance");
        second.GetAlerts("user-1").Should().ContainSingle()
            .Which.Symbol.Should().Be("BTCUSDT");
        second.GetServiceToggles().Alerts.Should().BeFalse();
        second.GetAdminSnapshot().UsersCount.Should().Be(1);
    }

    [Fact]
    public void Delete_operations_are_persisted_to_distributed_cache()
    {
        using var provider = CreateProvider();
        var cache = provider.GetRequiredService<IDistributedCache>();
        var options = CreateFlagsOptions();

        var first = new FrontendContractState(cache, options, NullLogger<FrontendContractState>.Instance);
        first.LinkExchange("user-1", new LinkExchangeRequest("binance", "abcd1234efgh5678"));
        var alert = first.CreateAlert("user-1", new CreateAlertRequest("ETHUSDT", "below", 3000m));

        var second = new FrontendContractState(cache, options, NullLogger<FrontendContractState>.Instance);
        second.DeleteExchange("user-1", "binance").Should().BeTrue();
        second.DeleteAlert("user-1", alert.Id).Should().BeTrue();

        var third = new FrontendContractState(cache, options, NullLogger<FrontendContractState>.Instance);
        third.GetLinkedExchanges("user-1").Should().BeEmpty();
        third.GetAlerts("user-1").Should().BeEmpty();
    }

    private static ServiceProvider CreateProvider()
    {
        var services = new ServiceCollection();
        services.AddDistributedMemoryCache();
        return services.BuildServiceProvider();
    }

    private static IOptions<FeatureFlagsSettings> CreateFlagsOptions() =>
        Options.Create(new FeatureFlagsSettings
        {
            Portfolio = true,
            Market = true,
            News = true,
            Notifications = true,
        });
}