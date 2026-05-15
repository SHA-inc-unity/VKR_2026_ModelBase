using FluentAssertions;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace GatewayService.UnitTests.Market;

/// <summary>Unit tests for ChartService — all downstream dependencies mocked.</summary>
public sealed class ChartServiceTests
{
    private static MarketSettings DefaultSettings() => new()
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

    // ── Builders ──────────────────────────────────────────────────────────

    private static Mock<IMarketConfigService> KnownSymbolConfig(string symbol = "BTCUSDT")
    {
        var m = new Mock<IMarketConfigService>();
        m.Setup(c => c.IsKnownSymbolAsync(It.Is<string>(s =>
                s.Equals(symbol, StringComparison.OrdinalIgnoreCase)),
            It.IsAny<CancellationToken>()))
         .ReturnsAsync(true);
        m.Setup(c => c.IsKnownSymbolAsync(It.Is<string>(s =>
                !s.Equals(symbol, StringComparison.OrdinalIgnoreCase)),
            It.IsAny<CancellationToken>()))
         .ReturnsAsync(false);
        return m;
    }

    private static IMarketCacheService EmptyCacheService()
    {
        var m = new Mock<IMarketCacheService>();
        m.Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync((ChartResponse?)null);
        m.Setup(c => c.GetAsync<string>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync((string?)null);
        m.Setup(c => c.SetIfNotExistsAsync(It.IsAny<string>(), It.IsAny<string>(),
                It.IsAny<TimeSpan>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync(true);
        return m.Object;
    }

    private static IMarketCacheService CacheWithIngestLock()
    {
        var m = new Mock<IMarketCacheService>();
        m.Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync((ChartResponse?)null);
        // Ingest lock key returns a value → ingest already in progress
        m.Setup(c => c.GetAsync<string>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync("1");
        return m.Object;
    }

    private static IMarketCacheService CacheWithHit(ChartResponse cached)
    {
        var m = new Mock<IMarketCacheService>();
        m.Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync(cached);
        return m.Object;
    }

    private static CoverageResult FullCoverage(long nowMs, int limit, long stepMs) => new(
        Exists:      true,
        TableName:   "btcusdt_5",
        Rows:        limit,
        MinTsMs:     nowMs - (limit - 1) * stepMs,
        MaxTsMs:     nowMs,
        CoveragePct: 1.0);

    private static IReadOnlyList<CandleRow> BuildRows(long endMs, int count, long stepMs) =>
        Enumerable.Range(0, count)
            .Select(i => new CandleRow(
                TimestampMs: endMs - (long)(count - 1 - i) * stepMs,
                Open: 40000m, High: 41000m, Low: 39000m, Close: 40500m,
                Volume: 100m, Turnover: 4050000m))
            .ToList();

    private ChartService CreateSut(
        IMarketConfigService? config = null,
        IMarketCacheService?  cache  = null,
        IDataServiceClient?   data   = null,
        MarketSettings?       settings = null)
    {
        config ??= KnownSymbolConfig().Object;
        cache  ??= EmptyCacheService();
        settings ??= DefaultSettings();

        if (data is null)
        {
            var dataMock = new Mock<IDataServiceClient>();
            dataMock.Setup(d => d.GetCoverageAsync(It.IsAny<string>(), It.IsAny<string>(),
                    It.IsAny<CancellationToken>()))
                .ReturnsAsync((CoverageResult?)null);
            data = dataMock.Object;
        }

        return new ChartService(config, cache, data,
            Options.Create(settings), NullLogger<ChartService>.Instance);
    }

    // ── Validation tests ──────────────────────────────────────────────────

    [Fact]
    public async Task Unknown_symbol_returns_failure()
    {
        var sut = CreateSut();
        var result = await sut.GetChartAsync("FAKEUSDT", "5m", 200);
        result.IsSuccess.Should().BeFalse();
        result.Error.Should().Contain("INVALID_SYMBOL");
    }

    [Fact]
    public async Task Invalid_timeframe_returns_failure()
    {
        var sut = CreateSut();
        var result = await sut.GetChartAsync("BTCUSDT", "99x", 200);
        result.IsSuccess.Should().BeFalse();
        result.Error.Should().Contain("INVALID_TIMEFRAME");
    }

    [Fact]
    public async Task Invalid_limit_for_heavy_timeframe_returns_failure()
    {
        var sut = CreateSut();
        // 2000 is not in the Heavy grid (max 500)
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 2000);
        result.IsSuccess.Should().BeFalse();
        result.Error.Should().Contain("INVALID_LIMIT");
    }

    [Fact]
    public async Task Valid_limit_for_heavy_timeframe_passes_validation()
    {
        // No data, but validation passes — should return pending
        var sut = CreateSut();
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);
        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("pending");
    }

    // ── Cache hit ─────────────────────────────────────────────────────────

    [Fact]
    public async Task Cache_hit_returns_cached_response_without_kafka_calls()
    {
        var cached = new ChartResponse
        {
            Symbol = "BTCUSDT", Timeframe = "5m", Limit = 200, Status = "ok"
        };

        var dataMock = new Mock<IDataServiceClient>();
        var sut = CreateSut(cache: CacheWithHit(cached), data: dataMock.Object);

        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeTrue();
        result.Value.Should().Be(cached);
        // data-service should NOT have been called
        dataMock.Verify(d => d.GetCoverageAsync(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<CancellationToken>()), Times.Never);
    }

    // ── No data → pending ─────────────────────────────────────────────────

    [Fact]
    public async Task No_coverage_data_returns_pending_status()
    {
        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetCoverageAsync("BTCUSDT", "5", It.IsAny<CancellationToken>()))
                .ReturnsAsync(new CoverageResult(false, "", 0, 0, 0, 0));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("pending");
        result.Value.Candles.Should().BeEmpty();
        result.Value.RetryAfterMs.Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task Ingest_in_progress_returns_pending_without_triggering_new_ingest()
    {
        var dataMock = new Mock<IDataServiceClient>();
        var sut = CreateSut(cache: CacheWithIngestLock(), data: dataMock.Object);

        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("pending");
        dataMock.Verify(d => d.FireAndForgetIngest(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<Action>(), It.IsAny<Action<Exception>>()), Times.Never);
    }

    // ── Full data → ok ────────────────────────────────────────────────────

    [Fact]
    public async Task Full_data_returns_ok_status_with_candles()
    {
        var nowMs  = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var stepMs = 300_000L; // 5m
        var limit  = 200;

        var coverage = FullCoverage(nowMs, limit, stepMs);
        var rows     = BuildRows(nowMs, limit, stepMs);

        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetCoverageAsync("BTCUSDT", "5", It.IsAny<CancellationToken>()))
                .ReturnsAsync(coverage);
        dataMock.Setup(d => d.GetRowsAsync("btcusdt_5", It.IsAny<long>(), It.IsAny<long>(),
                It.IsAny<int>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsResult.From(rows));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("ok");
        result.Value.Candles.Should().HaveCount(limit);
        result.Value.RetryAfterMs.Should().BeNull();
        result.Value.Meta.Coverage.Should().Be("full");
    }

    // ── Partial data → partial ────────────────────────────────────────────

    [Fact]
    public async Task Partial_data_returns_partial_status_with_retry_hint()
    {
        var nowMs  = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var stepMs = 300_000L;
        var limit  = 200;
        var available = 80; // fewer than requested

        var coverage = new CoverageResult(true, "btcusdt_5", available,
            nowMs - (long)(available - 1) * stepMs, nowMs, 0.40);
        var rows = BuildRows(nowMs, available, stepMs);

        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetCoverageAsync("BTCUSDT", "5", It.IsAny<CancellationToken>()))
                .ReturnsAsync(coverage);
        dataMock.Setup(d => d.GetRowsAsync("btcusdt_5", It.IsAny<long>(), It.IsAny<long>(),
                It.IsAny<int>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsResult.From(rows));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("partial");
        result.Value.Candles.Should().HaveCount(available);
        result.Value.RetryAfterMs.Should().BeGreaterThan(0);
        result.Value.Meta.Coverage.Should().Be("partial");
    }

    // ── Response metadata ─────────────────────────────────────────────────

    [Fact]
    public async Task Response_meta_from_and_to_ms_are_set_correctly()
    {
        var nowMs  = 1_700_000_000_000L;
        var stepMs = 300_000L;
        var limit  = 50;   // must be a valid Heavy-grid value
        var rows   = BuildRows(nowMs, limit, stepMs);

        var coverage = FullCoverage(nowMs, limit, stepMs);

        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetCoverageAsync("BTCUSDT", "5", It.IsAny<CancellationToken>()))
                .ReturnsAsync(coverage);
        dataMock.Setup(d => d.GetRowsAsync(It.IsAny<string>(), It.IsAny<long>(), It.IsAny<long>(),
                It.IsAny<int>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsResult.From(rows));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Meta.FromMs.Should().BeLessThan(result.Value.Meta.ToMs);
        result.Value.Meta.ToMs.Should().Be(rows[^1].TimestampMs);
        result.Value.Meta.Requested.Should().Be(limit);
        result.Value.Meta.Available.Should().Be(rows.Count);
    }
}
