using FluentAssertions;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Xunit;

namespace GatewayService.IntegrationTests;

/// <summary>
/// Partial-real pipeline integration tests for <see cref="ChartRequestQueue"/>.
///
/// Builds a minimal DI container with the real <see cref="ChartRequestQueue"/> +
/// <see cref="ChartService"/> wired to fake downstream dependencies
/// (no Kafka, no Redis, no Bybit).
///
/// Verifies coalescing, independent keys and cancellation isolation end-to-end
/// through the actual production code paths.
/// </summary>
public sealed class MarketQueueIntegrationTests : IClassFixture<MarketQueueContainer>, IDisposable
{
    private readonly MarketQueueContainer _container;

    public MarketQueueIntegrationTests(MarketQueueContainer container)
    {
        _container = container;
    }

    public void Dispose() { } // container lifetime managed by IClassFixture

    private IChartService Chart => _container.Chart;

    // ── Tests ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task Concurrent_identical_requests_coalesce_to_one_downstream_call()
    {
        const int n = 50;
        var chart = Chart;

        var tasks = Enumerable.Range(0, n)
            .Select(_ => chart.GetChartAsync("BTCUSDT", "5m", 200))
            .ToArray();

        var results = await Task.WhenAll(tasks);

        results.Should().AllSatisfy(r => r.IsSuccess.Should().BeTrue());

        _container.FakeData.GetLatestWindowCallCount
            .Should().BeLessOrEqualTo(
                n / 5 + 2,
                "concurrent identical requests should be heavily coalesced");
    }

    [Fact]
    public async Task Different_keys_resolved_independently()
    {
        var chart = Chart;

        var tasks = new[]
        {
            chart.GetChartAsync("BTCUSDT", "5m",  200),
            chart.GetChartAsync("ETHUSDT", "5m",  200),
            chart.GetChartAsync("BTCUSDT", "60m", 100),
        };

        var results = await Task.WhenAll(tasks);
        results.Should().AllSatisfy(r => r.IsSuccess.Should().BeTrue());
    }

    [Fact]
    public async Task Cancellation_of_one_client_does_not_affect_others()
    {
        var chart = Chart;

        // Start the creator (no CT) and yield so it proceeds far enough to register
        // itself in the in-flight dictionary before the waiter starts.
        var creatorTask = chart.GetChartAsync("BTCUSDT", "5m", 200);
        await Task.Yield();

        // Pre-cancel the token BEFORE creating the waiter task so that
        // WaitAsync(cancelledCT) on the still-pending TCS throws immediately.
        using var cts = new CancellationTokenSource();
        cts.Cancel();

        // Create the waiter with the pre-cancelled token.
        var waiterTask = chart.GetChartAsync("BTCUSDT", "5m", 200, cts.Token);

        // Waiter must throw OperationCanceledException immediately.
        Func<Task> act = () => waiterTask;
        await act.Should().ThrowAsync<OperationCanceledException>(
            "a pre-cancelled waiter must not block other clients");

        // The creator (which owns workCts, not cts) must still complete successfully.
        var result = await creatorTask;
        result.IsSuccess.Should().BeTrue("the creator pipeline must not be affected");
    }
}

// ── Fixture: manual DI container ─────────────────────────────────────────────

/// <summary>
/// Builds and owns a minimal <see cref="ServiceProvider"/> with the real
/// <see cref="ChartRequestQueue"/> + <see cref="ChartService"/> and fake downstream.
/// </summary>
public sealed class MarketQueueContainer : IDisposable
{
    private readonly ServiceProvider _provider;
    public readonly FakeDelayedDataServiceClient FakeData = new();

    public IChartService Chart => _provider.GetRequiredService<IChartService>();

    public MarketQueueContainer()
    {
        var settings = new MarketSettings
        {
            KafkaTimeoutSeconds        = 5,
            IngestKafkaTimeoutSeconds  = 30,
            IngestRetryAfterMs         = 5000,
            IngestLockTtlSeconds       = 60,
            FullCoverageThreshold      = 0.99,
            IngestWindowMultiplier     = 3,
            IngestErrorCooldownSeconds = 30,
            ChartCacheTtlHeavySeconds  = 30,
            ChartCacheTtlMediumSeconds = 120,
            ChartCacheTtlLightSeconds  = 300,
            QueueTotalConcurrency      = 10,
            QueueHeavyConcurrency      = 3,
            QueueMaxWaitSeconds        = 5,
        };

        var services = new ServiceCollection();
        services.AddLogging();
        services.AddSingleton(Options.Create(settings));
        services.AddSingleton<IMarketCacheService, NullMarketCacheService>();
        services.AddSingleton<IMarketConfigService, AlwaysKnownSymbolService>();
        services.AddSingleton<IDataServiceClient>(FakeData);
        services.AddSingleton<IBybitSymbolProvider>(NopBybitProvider.Instance);
        services.AddSingleton<ChartService>();
        services.AddSingleton<IChartService, ChartRequestQueue>();

        _provider = services.BuildServiceProvider();
    }

    public void Dispose() => _provider.Dispose();
}

// ── Test doubles ──────────────────────────────────────────────────────────────

/// <summary>Always treats any symbol as known; returns empty config.</summary>
internal sealed class AlwaysKnownSymbolService : IMarketConfigService
{
    public Task<bool> IsKnownSymbolAsync(string symbol, CancellationToken ct = default)
        => Task.FromResult(true);

    public Task<MarketConfigResponse> GetConfigAsync(CancellationToken ct = default)
        => Task.FromResult(new MarketConfigResponse
        {
            Symbols    = [],
            Timeframes = [],
            CandleCounts = new CandleCountConstraintsDto
            {
                Heavy = [], Medium = [], Light = [],
                HeavyTimeframes = [], MediumTimeframes = [], LightTimeframes = [],
            },
            Defaults = new MarketDefaultsDto("BTCUSDT", "5m", 200),
            CachedAt = DateTimeOffset.UtcNow,
            SymbolsUpdatedAt = DateTimeOffset.UtcNow,
        });
}

/// <summary>Cache that always misses and discards writes.</summary>
internal sealed class NullMarketCacheService : IMarketCacheService
{
    public Task<T?> GetAsync<T>(string key, CancellationToken ct = default) where T : class
        => Task.FromResult<T?>(null);
    public Task SetAsync<T>(string key, T value, TimeSpan ttl, CancellationToken ct = default) where T : class
        => Task.CompletedTask;
    public Task<bool> SetIfNotExistsAsync(string key, string value, TimeSpan ttl, CancellationToken ct = default)
        => Task.FromResult(false); // lock never acquired → no background ingest
    public Task RemoveAsync(string key, CancellationToken ct = default)
        => Task.CompletedTask;
    public async Task<T> GetOrCreateAsync<T>(string key, TimeSpan ttl,
        Func<Task<T>> factory, CancellationToken ct = default) where T : class
        => await factory();
}

/// <summary>No-op Bybit provider; not used by ChartService directly.</summary>
internal sealed class NopBybitProvider : IBybitSymbolProvider
{
    public static readonly NopBybitProvider Instance = new();
    public Task<IReadOnlyList<string>> GetActiveSymbolsAsync(CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<string>>([]);
}

/// <summary>
/// Instrumented data client with artificial delay so concurrent requests coalesce.
/// </summary>
public sealed class FakeDelayedDataServiceClient : IDataServiceClient
{
    private int _getLatestWindowCallCount;
    public int GetLatestWindowCallCount => _getLatestWindowCallCount;

    public async Task<CoverageResult?> GetCoverageAsync(
        string symbol, string bybitInterval, CancellationToken ct = default)
        => null;

    public async Task<RowsResult> GetLatestWindowRowsAsync(
        string symbol,
        string bybitInterval,
        long stepMs,
        int limit,
        CancellationToken ct = default)
    {
        Interlocked.Increment(ref _getLatestWindowCallCount);
        await Task.Delay(200, ct); // deliberate delay so concurrent requests coalesce
        return RowsResult.Empty; // no latest rows → ChartService returns pending (no rows path)
    }

    public Task<RowsResult> GetRowsAsync(
        string tableName, long startMs, long endMs, int limit, CancellationToken ct = default)
        => Task.FromResult(RowsResult.Empty);

    public Task<IngestResult> IngestAsync(
        string symbol, string bybitInterval, long startMs, long endMs, CancellationToken ct = default)
        => Task.FromResult(IngestResult.Fail("disabled"));

    public void FireAndForgetIngest(
        string symbol, string bybitInterval, long startMs, long endMs,
        Action onComplete, Action<Exception> onError)
    {
        // No-op — ingest lock never acquired (SetIfNotExistsAsync returns false)
    }
}
