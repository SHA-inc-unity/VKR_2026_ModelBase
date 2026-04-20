using FluentAssertions;
using GatewayService.API.Aggregators.Bootstrap;
using Xunit;
using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;
using GatewayService.API.Settings;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;

namespace GatewayService.UnitTests;

public sealed class BootstrapAggregatorTests
{
    private readonly Mock<IAccountServiceClient> _accountMock = new();
    private readonly FeatureFlagsSettings _flags = new()
    {
        Portfolio = true,
        Market = true,
        News = true,
        Notifications = false
    };

    private BootstrapAggregator CreateSut() =>
        new(_accountMock.Object,
            Options.Create(_flags),
            NullLogger<BootstrapAggregator>.Instance);

    [Fact]
    public async Task No_token_skips_account_call_and_returns_null_user()
    {
        var sut = CreateSut();

        var result = await sut.AggregateAsync(null);

        result.User.Should().BeNull();
        result.DegradedServices.Should().BeEmpty();
        _accountMock.Verify(c => c.GetCurrentUserAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Valid_token_returns_user_summary()
    {
        var dto = new AccountUserDto
        {
            Id = Guid.NewGuid(),
            Email = "alice@example.com",
            Username = "alice",
            Status = "active",
            Roles = ["user"],
            CreatedAt = DateTimeOffset.UtcNow
        };
        _accountMock
            .Setup(c => c.GetCurrentUserAsync("tok", default))
            .ReturnsAsync(ServiceResult<AccountUserDto>.Ok(dto));

        var sut = CreateSut();
        var result = await sut.AggregateAsync("tok");

        result.User.Should().NotBeNull();
        result.User!.Email.Should().Be("alice@example.com");
        result.DegradedServices.Should().BeEmpty();
    }

    [Fact]
    public async Task Account_failure_marks_service_degraded_and_user_is_null()
    {
        _accountMock
            .Setup(c => c.GetCurrentUserAsync("tok", default))
            .ReturnsAsync(ServiceResult<AccountUserDto>.Fail("timeout"));

        var sut = CreateSut();
        var result = await sut.AggregateAsync("tok");

        result.User.Should().BeNull();
        result.DegradedServices.Should().Contain("account");
        result.SystemStatus.Status.Should().Be("degraded");
    }

    [Fact]
    public async Task Feature_flags_are_propagated()
    {
        var sut = CreateSut();

        var result = await sut.AggregateAsync(null);

        result.FeatureFlags.Portfolio.Should().BeTrue();
        result.FeatureFlags.Notifications.Should().BeFalse();
    }
}
