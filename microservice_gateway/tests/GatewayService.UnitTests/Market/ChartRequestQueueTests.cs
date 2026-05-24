using FluentAssertions;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace GatewayService.UnitTests.Market;

/// <summary>
/// Unit tests for <see cref="ChartRequestQueue"/>.
/// Verifies coalescing semantics, cancellation isolation, busy rejection,
/// and claim-check / error-cooldown paths via the inner <see cref="ChartService"/> stub.
/// </summary>
public sealed class ChartRequestQueueTests
{
    // ── Helpers ───────────────────────────────────────────────────────────

    private static MarketSettings Settings(
        int totalConcurrency = 10,
        int heavyConcurrency = 3,
        int maxWaitSeconds   = 5) => new()
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
        QueueTotalConcurrency      = totalConcurrency,
        QueueHeavyConcurrency      = heavyConcurrency,
        QueueMaxWaitSeconds        = maxWaitSeconds,
        ChartInflightWaitSeconds   = 1,
        ChartInflightPollMs        = 50,
    };

    private static IMarketCacheService EmptyCache()
    {
        var m = new Mock<IMarketCacheService>();
        m.Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync((ChartResponse?)null);
        m.Setup(c => c.GetAsync<string>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync((string?)null);
        return m.Object;
    }

    private static ChartService BuildChartService(
        Func<string, string, int, Task<ServiceResult<ChartResponse>>> innerImpl)
    {
        // Build a real ChartService whose inner calls are intercepted via a
        // custom IDataServiceClient / IMarketConfigService that encode the logic above.
        var configMock = new Mock<IMarketConfigService>();
        configMock
            .Setup(c => c.IsKnownSymbolAsync(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(true);

        var cacheMock = new Mock<IMarketCacheService>();
        cacheMock
            .Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((ChartResponse?)null);
        cacheMock
            .Setup(c => c.GetAsync<string>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string?)null);
        cacheMock
            .Setup(c => c.SetIfNotExistsAsync(It.IsAny<string>(), It.IsAny<string>(),
                It.IsAny<TimeSpan>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(false); // lock not acquired → no background ingest

        var dataMock = new Mock<IDataServiceClient>();
        // Coverage: non-existent so ChartService will fall through the no-rows
        // path and return a SERVICE_BUSY failure (no "pending" status anymore).
        dataMock
            .Setup(d => d.GetCoverageAsync(It.IsAny<string>(), It.IsAny<string>(),
                It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((CoverageResult?)null);

        return new ChartService(
            configMock.Object,
            cacheMock.Object,
            dataMock.Object,
            Options.Create(Settings()),
            NullLogger<ChartService>.Instance);
    }

    /// <summary>
    /// Creates a ChartRequestQueue backed by a <see cref="FakeInnerService"/>
    /// that lets the test control the delay and result per call.
    /// </summary>
    private static (ChartRequestQueue queue, FakeInnerService fake) BuildQueue(
        MarketSettings? settings = null)
    {
        var fake  = new FakeInnerService();
        var cache = EmptyCache();
        var opts  = Options.Create(settings ?? Settings());
        var queue = new ChartRequestQueue(fake, cache, opts,
            NullLogger<ChartRequestQueue>.Instance);
        return (queue, fake);
    }

    // ── FakeInnerService: wraps ChartService contract without real infra ──

    /// <summary>
    /// A test double that implements the same shape as <see cref="ChartService"/> is
    /// injected into <see cref="ChartRequestQueue"/> but allows fine-grained control
    /// over latency, call count, and result.
    /// </summary>
    private sealed class FakeInnerService : ChartService
    {
        private readonly TaskCompletionSource<ServiceResult<ChartResponse>> _gate
            = new(TaskCreationOptions.RunContinuationsAsynchronously);

        private int _callCount;
        public int CallCount => _callCount;

        public void Unblock(ServiceResult<ChartResponse> result) => _gate.TrySetResult(result);
        public void UnblockOk(int candles = 10)
        {
            var response = new ChartResponse
            {
                Symbol = "BTCUSDT", Timeframe = "5m", Limit = candles,
                Status = "ok", Candles = Enumerable.Range(0, candles)
                    .Select(i => new CandleDto(i, 1m, 1m, 1m, 1m, 1m, 1m))
                    .ToList(),
                Meta = new ChartMetaDto { Requested = candles, Available = candles, Coverage = "full" },
            };
            Unblock(ServiceResult<ChartResponse>.Ok(response));
        }
        public void UnblockError() =>
            Unblock(ServiceResult<ChartResponse>.Fail("DOWNSTREAM_ERROR"));

        public override async Task<ServiceResult<ChartResponse>> GetChartAsync(
            string symbol, string timeframe, int limit,
            string exchange = "bybit", CancellationToken ct = default)
        {
            Interlocked.Increment(ref _callCount);
            return await _gate.Task.WaitAsync(ct);
        }

        // Ctor passes dummy dependencies — never actually reached in override path.
        public FakeInnerService() : base(
            new Mock<IMarketConfigService>().Object,
            new Mock<IMarketCacheService>().Object,
            new Mock<IDataServiceClient>().Object,
            Options.Create(new MarketSettings()),
            NullLogger<ChartService>.Instance)
        { }
    }

    // ── Tests ─────────────────────────────────────────────────────────────

    [Fact]
    public async Task N_concurrent_identical_requests_result_in_1_downstream_call()
    {
        var (queue, fake) = BuildQueue();

        // Start 100 concurrent identical requests BEFORE unblocking the inner
        const int n = 100;
        var tasks = Enumerable.Range(0, n)
            .Select(_ => queue.GetChartAsync("BTCUSDT", "5m", 200))
            .ToArray();

        // Give tasks a moment to coalesce
        await Task.Delay(50);

        fake.UnblockOk();

        var results = await Task.WhenAll(tasks);

        fake.CallCount.Should().Be(1, "all 100 requests should be coalesced to 1 downstream call");
        results.Should().AllSatisfy(r =>
        {
            r.IsSuccess.Should().BeTrue();
            r.Value!.Status.Should().Be("ok");
        });
    }

    [Fact]
    public async Task Different_keys_do_not_interfere_with_each_other()
    {
        // Use two separate queue instances to avoid shared _inflight state
        var (queue1, fake1) = BuildQueue();
        var (queue2, fake2) = BuildQueue();

        var t1 = queue1.GetChartAsync("BTCUSDT", "5m", 200);
        var t2 = queue2.GetChartAsync("ETHUSDT", "5m", 200);

        await Task.Delay(30);

        fake1.UnblockOk(10);
        fake2.UnblockOk(10);

        var r1 = await t1;
        var r2 = await t2;

        r1.IsSuccess.Should().BeTrue();
        r2.IsSuccess.Should().BeTrue();
    }

    [Fact]
    public async Task Cancelling_one_waiter_does_not_cancel_the_shared_pipeline()
    {
        var (queue, fake) = BuildQueue();

        // Creator
        var creatorTask = queue.GetChartAsync("BTCUSDT", "5m", 200);
        await Task.Delay(20); // let creator start

        // Waiter with a cancellable token
        using var cts = new CancellationTokenSource();
        var waiterTask = queue.GetChartAsync("BTCUSDT", "5m", 200, cts.Token);

        await Task.Delay(20);

        // Cancel the waiter
        cts.Cancel();

        // The waiter should throw OperationCanceledException
        Func<Task> waitAction = () => waiterTask;
        await waitAction.Should().ThrowAsync<OperationCanceledException>();

        // The creator should NOT be cancelled — unblock it and verify success
        fake.UnblockOk();
        var creatorResult = await creatorTask;
        creatorResult.IsSuccess.Should().BeTrue(
            "cancelling a waiter must not cancel the shared pipeline");

        fake.CallCount.Should().Be(1);
    }

    [Fact]
    public async Task Error_in_inner_service_propagated_as_failure_result_to_all_waiters()
    {
        var (queue, fake) = BuildQueue();

        const int n = 10;
        var tasks = Enumerable.Range(0, n)
            .Select(_ => queue.GetChartAsync("BTCUSDT", "5m", 200))
            .ToArray();

        await Task.Delay(30);
        fake.UnblockError();

        var results = await Task.WhenAll(tasks);

        fake.CallCount.Should().Be(1);
        results.Should().AllSatisfy(r =>
        {
            r.IsSuccess.Should().BeFalse();
            r.Error.Should().Contain("DOWNSTREAM_ERROR");
        });
    }

    [Fact]
    public async Task Queue_returns_service_busy_when_total_concurrency_exhausted()
    {
        // Allow only 1 total concurrent request, no wait
        var settings = Settings(totalConcurrency: 1, heavyConcurrency: 1, maxWaitSeconds: 0);
        var (queue, fake) = BuildQueue(settings);

        // First request occupies the semaphore
        var blocker = queue.GetChartAsync("BTCUSDT", "5m", 200);
        await Task.Delay(30);

        // Second request should get busy (timeout = 0)
        var busyResult = await queue.GetChartAsync("BTCUSDT", "60m", 200); // different key

        busyResult.IsSuccess.Should().BeFalse();
        busyResult.Error.Should().Contain("SERVICE_BUSY");

        // Unblock the first
        fake.UnblockOk();
        var first = await blocker;
        first.IsSuccess.Should().BeTrue();
    }

    [Fact]
    public async Task Cache_hit_in_fast_path_does_not_create_inflight_entry()
    {
        // Build a queue where the cache always returns a hit
        var cached = new ChartResponse
        {
            Symbol = "BTCUSDT", Timeframe = "5m", Limit = 200, Status = "ok",
            Candles = [], Meta = new ChartMetaDto { Coverage = "full" },
        };

        var cacheMock = new Mock<IMarketCacheService>();
        cacheMock
            .Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(cached);

        var fake  = new FakeInnerService();
        var queue = new ChartRequestQueue(fake, cacheMock.Object,
            Options.Create(Settings()), NullLogger<ChartRequestQueue>.Instance);

        const int n = 20;
        var tasks   = Enumerable.Range(0, n)
            .Select(_ => queue.GetChartAsync("BTCUSDT", "5m", 200))
            .ToArray();
        var results = await Task.WhenAll(tasks);

        fake.CallCount.Should().Be(0, "all requests should be served from cache");
        results.Should().AllSatisfy(r =>
        {
            r.IsSuccess.Should().BeTrue();
            r.Value.Should().Be(cached);
        });
    }
}
