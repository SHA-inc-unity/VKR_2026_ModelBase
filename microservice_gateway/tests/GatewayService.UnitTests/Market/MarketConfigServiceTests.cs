using FluentAssertions;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace GatewayService.UnitTests.Market;

public sealed class MarketConfigServiceTests
{
    private static MarketSettings DefaultSettings() => new()
    {
        DefaultSymbol      = "BTCUSDT",
        DefaultTimeframe   = "5m",
        DefaultCandleCount = 200,
        SymbolsCacheTtlSeconds = 3600,
        ConfigCacheTtlSeconds  = 3600,
    };

    private static IOptions<MarketSettings> Options(MarketSettings? s = null) =>
        Microsoft.Extensions.Options.Options.Create(s ?? DefaultSettings());

    // ── Helpers ───────────────────────────────────────────────────────────

    /// <summary>
    /// A pass-through IMarketCacheService implementation that always calls the factory
    /// (bypasses Redis). Works with any T — no need to reference private inner types.
    /// </summary>
    private sealed class PassthroughCache : IMarketCacheService
    {
        public Task<T?> GetAsync<T>(string key, CancellationToken ct = default) where T : class
            => Task.FromResult<T?>(null);

        public Task SetAsync<T>(string key, T value, TimeSpan ttl, CancellationToken ct = default) where T : class
            => Task.CompletedTask;

        public Task<bool> SetIfNotExistsAsync(string key, string value, TimeSpan ttl, CancellationToken ct = default)
            => Task.FromResult(true);

        public Task RemoveAsync(string key, CancellationToken ct = default)
            => Task.CompletedTask;

        public async Task<T> GetOrCreateAsync<T>(string key, TimeSpan ttl,
            Func<Task<T>> factory, CancellationToken ct = default) where T : class
            => await factory();
    }

    private static IBybitSymbolProvider SymbolProvider(IReadOnlyList<string>? symbols = null)
    {
        var list = symbols ?? ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
        var mock = new Mock<IBybitSymbolProvider>();
        mock.Setup(p => p.GetActiveSymbolsAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(list);
        return mock.Object;
    }

    // ── Tests ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task GetConfigAsync_includes_all_eleven_timeframes()
    {
        var sut = new MarketConfigService(
            SymbolProvider(), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        var config = await sut.GetConfigAsync();

        config.Timeframes.Should().HaveCount(11);
    }

    [Fact]
    public async Task GetConfigAsync_returns_symbols_from_provider()
    {
        var symbols = new[] { "BTCUSDT", "ETHUSDT" };
        var sut = new MarketConfigService(
            SymbolProvider(symbols), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        var config = await sut.GetConfigAsync();

        config.Symbols.Should().BeEquivalentTo(symbols);
    }

    [Fact]
    public async Task GetConfigAsync_defaults_match_settings()
    {
        var settings = DefaultSettings();
        var sut = new MarketConfigService(
            SymbolProvider(), new PassthroughCache(), Options(settings),
            NullLogger<MarketConfigService>.Instance);

        var config = await sut.GetConfigAsync();

        config.Defaults.Symbol.Should().Be(settings.DefaultSymbol);
        config.Defaults.Timeframe.Should().Be(settings.DefaultTimeframe);
        config.Defaults.CandleCount.Should().Be(settings.DefaultCandleCount);
    }

    [Fact]
    public async Task GetConfigAsync_candle_counts_grouped_by_class()
    {
        var sut = new MarketConfigService(
            SymbolProvider(), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        var config = await sut.GetConfigAsync();

        config.CandleCounts.Heavy.Should().BeEquivalentTo(CandleCountGrid.Heavy);
        config.CandleCounts.Medium.Should().BeEquivalentTo(CandleCountGrid.Medium);
        config.CandleCounts.Light.Should().BeEquivalentTo(CandleCountGrid.Light);
    }

    [Fact]
    public async Task GetConfigAsync_heavy_timeframes_list_contains_5m()
    {
        var sut = new MarketConfigService(
            SymbolProvider(), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        var config = await sut.GetConfigAsync();

        config.CandleCounts.HeavyTimeframes.Should().Contain("5m");
    }

    [Fact]
    public async Task IsKnownSymbolAsync_returns_true_for_known_symbol()
    {
        var sut = new MarketConfigService(
            SymbolProvider(["BTCUSDT", "ETHUSDT"]), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        (await sut.IsKnownSymbolAsync("BTCUSDT")).Should().BeTrue();
    }

    [Fact]
    public async Task IsKnownSymbolAsync_is_case_insensitive()
    {
        var sut = new MarketConfigService(
            SymbolProvider(["BTCUSDT"]), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        (await sut.IsKnownSymbolAsync("btcusdt")).Should().BeTrue();
    }

    [Fact]
    public async Task IsKnownSymbolAsync_returns_false_for_unknown_symbol()
    {
        var sut = new MarketConfigService(
            SymbolProvider(["BTCUSDT"]), new PassthroughCache(), Options(),
            NullLogger<MarketConfigService>.Instance);

        (await sut.IsKnownSymbolAsync("FAKEUSDT")).Should().BeFalse();
    }
}
