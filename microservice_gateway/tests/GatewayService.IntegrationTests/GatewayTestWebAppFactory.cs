using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
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

            // Replace real market services with in-memory fakes so integration tests
            // don't need Kafka, Redis or a live Bybit connection.
            services.RemoveAll<IBybitSymbolProvider>();
            services.RemoveAll<IMarketConfigService>();
            services.RemoveAll<IChartService>();
            services.RemoveAll<IMarketCacheService>();

            services.AddSingleton<IMarketCacheService, NoopMarketCacheService>();
            services.AddSingleton<IBybitSymbolProvider>(
                new FakeBybitSymbolProvider(["BTCUSDT", "ETHUSDT", "SOLUSDT"]));
            services.AddSingleton<IMarketConfigService, FakeMarketConfigService>();
            services.AddSingleton<IChartService, FakeChartService>();
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

internal sealed class FakeBybitSymbolProvider : IBybitSymbolProvider
{
    private readonly IReadOnlyList<string> _symbols;
    public FakeBybitSymbolProvider(IReadOnlyList<string> symbols) => _symbols = symbols;
    public Task<IReadOnlyList<string>> GetActiveSymbolsAsync(CancellationToken ct = default) =>
        Task.FromResult(_symbols);
}

/// <summary>Noop IMarketCacheService — always misses, writes are discarded.</summary>
internal sealed class NoopMarketCacheService : IMarketCacheService
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

internal sealed class FakeMarketConfigService : IMarketConfigService
{
    private static readonly IReadOnlyList<string> Symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];

    public Task<MarketConfigResponse> GetConfigAsync(CancellationToken ct = default)
    {
        var config = new MarketConfigResponse
        {
            Symbols    = Symbols,
            Timeframes = TimeframeMap.All
                .Select(tf => new TimeframeDto(tf.Id, tf.Label,
                    tf.Class.ToString().ToLowerInvariant(), (int)tf.StepMs))
                .ToList(),
            CandleCounts = new CandleCountConstraintsDto
            {
                Heavy           = CandleCountGrid.Heavy,
                Medium          = CandleCountGrid.Medium,
                Light           = CandleCountGrid.Light,
                HeavyTimeframes  = TimeframeMap.All.Where(t => t.Class == TimeframeClass.Heavy ).Select(t => t.Id).ToList(),
                MediumTimeframes = TimeframeMap.All.Where(t => t.Class == TimeframeClass.Medium).Select(t => t.Id).ToList(),
                LightTimeframes  = TimeframeMap.All.Where(t => t.Class == TimeframeClass.Light ).Select(t => t.Id).ToList(),
            },
            Defaults     = new MarketDefaultsDto("BTCUSDT", "5m", 200),
            CachedAt     = DateTimeOffset.UtcNow,
            SymbolsUpdatedAt = DateTimeOffset.UtcNow,
        };
        return Task.FromResult(config);
    }

    public Task<bool> IsKnownSymbolAsync(string symbol, CancellationToken ct = default) =>
        Task.FromResult(Symbols.Contains(symbol, StringComparer.OrdinalIgnoreCase));
}

internal sealed class FakeChartService : IChartService
{
    public Task<ServiceResult<ChartResponse>> GetChartAsync(
        string symbol, string timeframe, int limit, CancellationToken ct = default)
    {
        if (!new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT" }
                .Contains(symbol, StringComparer.OrdinalIgnoreCase))
            return Task.FromResult(ServiceResult<ChartResponse>.Fail($"INVALID_SYMBOL: '{symbol}'"));

        if (!TimeframeMap.IsValid(timeframe))
            return Task.FromResult(ServiceResult<ChartResponse>.Fail($"INVALID_TIMEFRAME: '{timeframe}'"));

        if (!TimeframeMap.TryGetById(timeframe, out var tf) ||
            !CandleCountGrid.IsValid(limit, tf.Class))
            return Task.FromResult(ServiceResult<ChartResponse>.Fail($"INVALID_LIMIT: {limit}"));

        var now    = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var candles = Enumerable.Range(0, limit)
            .Select(i => new CandleDto(
                T:  now - (long)(limit - 1 - i) * tf.StepMs,
                O:  40000m, H: 41000m, L: 39000m, C: 40500m,
                V:  100m,   Tv: 4050000m))
            .ToList();

        var resp = new ChartResponse
        {
            Symbol    = symbol.ToUpperInvariant(),
            Timeframe = timeframe,
            Limit     = limit,
            Candles   = candles,
            Meta = new ChartMetaDto
            {
                Requested = limit,
                Available = candles.Count,
                FromMs    = candles[0].T,
                ToMs      = candles[^1].T,
                Coverage  = "full",
            },
            Status       = "ok",
            RetryAfterMs = null,
        };
        return Task.FromResult(ServiceResult<ChartResponse>.Ok(resp));
    }
}
