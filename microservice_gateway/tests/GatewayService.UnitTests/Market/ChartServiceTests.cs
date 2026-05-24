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
        // Tight wait budgets so tests do not block on the inflight-ingest
        // polling loop (production defaults are 45s × 750ms).
        ChartInflightWaitSeconds   = 1,
        ChartInflightPollMs        = 50,
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

    private static IMarketCacheService CacheWithIngestErrorCooldown()
    {
        var m = new Mock<IMarketCacheService>();
        m.Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync((ChartResponse?)null);
        m.Setup(c => c.GetAsync<string>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
         .ReturnsAsync("error_cooldown");
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
            dataMock.Setup(d => d.GetLatestWindowRowsAsync(
                    It.IsAny<string>(), It.IsAny<string>(), It.IsAny<long>(), It.IsAny<int>(),
                    It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsFetchResult.Empty);
            dataMock.Setup(d => d.GetCoverageAsync(It.IsAny<string>(), It.IsAny<string>(),
                    It.IsAny<string>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync((CoverageResult?)null);
            dataMock.Setup(d => d.IngestAsync(It.IsAny<string>(), It.IsAny<string>(),
                    It.IsAny<long>(), It.IsAny<long>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(IngestResult.InProgress(errorDetail: "ingest still running"));
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
        // No data + ingest stuck "in progress" → backend waits then returns
        // a SERVICE_BUSY failure rather than a fake "pending" success.
        var sut = CreateSut();
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);
        result.IsSuccess.Should().BeFalse();
        result.Error.Should().StartWith("SERVICE_BUSY:");
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
        dataMock.Verify(d => d.GetLatestWindowRowsAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<long>(), It.IsAny<int>(),
            It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Larger_cached_window_can_satisfy_smaller_limit_without_kafka_calls()
    {
        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var stepMs = 300_000L;
        var cachedCandles = BuildRows(nowMs, 200, stepMs)
            .Select(row => new CandleDto(
                row.TimestampMs,
                row.Open,
                row.High,
                row.Low,
                row.Close,
                row.Volume,
                row.Turnover))
            .ToArray();

        var cached = new ChartResponse
        {
            Symbol = "BTCUSDT",
            Timeframe = "5m",
            Limit = 200,
            Status = "ok",
            Candles = cachedCandles,
            Meta = new ChartMetaDto
            {
                Requested = 200,
                Available = 200,
                FromMs = cachedCandles[0].T,
                ToMs = cachedCandles[^1].T,
                Coverage = "full",
            },
        };

        var cacheMock = new Mock<IMarketCacheService>();
        cacheMock.Setup(c => c.GetAsync<ChartResponse>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string key, CancellationToken _) =>
                key.EndsWith(":200:v2", StringComparison.Ordinal) ? cached : null);
        cacheMock.Setup(c => c.GetAsync<string>(It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync((string?)null);
        cacheMock.Setup(c => c.SetAsync(
                It.IsAny<string>(),
                It.IsAny<ChartResponse>(),
                It.IsAny<TimeSpan>(),
                It.IsAny<CancellationToken>()))
            .Returns(Task.CompletedTask);

        var dataMock = new Mock<IDataServiceClient>();
        var sut = CreateSut(cache: cacheMock.Object, data: dataMock.Object);

        var result = await sut.GetChartAsync("BTCUSDT", "5m", 50);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("ok");
        result.Value.Candles.Should().HaveCount(50);
        result.Value.Meta.Requested.Should().Be(50);
        result.Value.Meta.Available.Should().Be(50);

        cacheMock.Verify(c => c.SetAsync(
            It.Is<string>(key => key.EndsWith(":50:v2", StringComparison.Ordinal)),
            It.IsAny<ChartResponse>(),
            It.IsAny<TimeSpan>(),
            It.IsAny<CancellationToken>()), Times.Once);
        dataMock.Verify(d => d.GetLatestWindowRowsAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<long>(), It.IsAny<int>(),
            It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    // ── No data → pending ─────────────────────────────────────────────────

    [Fact]
    public async Task No_latest_window_data_triggers_sync_ingest_and_returns_ok_when_rows_arrive()
    {
        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var stepMs = 300_000L;
        var limit = 200;
        var rows = BuildRows(nowMs, limit, stepMs);

        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
                "BTCUSDT", "5", stepMs, limit, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsFetchResult.Empty);
        dataMock.Setup(d => d.IngestAsync("BTCUSDT", "5", It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(IngestResult.Ok("btcusdt_5", limit));
        dataMock.Setup(d => d.GetRowsAsync("btcusdt_5", It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<int>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.From(rows));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("ok");
        result.Value.Candles.Should().HaveCount(limit);
        result.Value.RetryAfterMs.Should().BeNull();
        dataMock.Verify(d => d.IngestAsync(
            "BTCUSDT", "5", It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Once);
        dataMock.Verify(d => d.GetRowsAsync(
            "btcusdt_5", It.IsAny<long>(), It.IsAny<long>(), limit,
            It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()), Times.Once);
    }

    [Fact]
    public async Task No_latest_window_returns_service_busy_when_sync_ingest_is_still_running()
    {
        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
                "BTCUSDT", "5", 300_000L, 200, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.Empty);
        dataMock.Setup(d => d.IngestAsync(
                "BTCUSDT", "5", It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(IngestResult.InProgress(errorDetail: "ingest job 42 is still running"));

        // Tight budget so the test does not hang waiting for ingest to finish.
        var settings = DefaultSettings();

        var sut = CreateSut(data: dataMock.Object, settings: settings);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeFalse();
        result.Error.Should().StartWith("SERVICE_BUSY:");
    }

    [Fact]
    public async Task Ingest_in_progress_waits_and_returns_service_busy_when_rows_never_arrive()
    {
        var dataMock = new Mock<IDataServiceClient>();
        // Latest window remains empty for the full polling budget — emulate a
        // long-running ingest that we eventually give up on.
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
                "BTCUSDT", "5", 300_000L, 200, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.Empty);

        var sut = CreateSut(cache: CacheWithIngestLock(), data: dataMock.Object);

        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeFalse();
        result.Error.Should().StartWith("SERVICE_BUSY:");
        // The gateway must NOT kick off a duplicate ingest while another one
        // is already in flight (that is the whole point of the ingest lock).
        dataMock.Verify(d => d.IngestAsync(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
        dataMock.Verify(d => d.FireAndForgetIngest(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<Action>(), It.IsAny<Action<Exception>>(), It.IsAny<string>()), Times.Never);
    }

    [Fact]
    public async Task Latest_window_claim_check_returns_data_source_unavailable()
    {
        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
                "BTCUSDT", "5", 300_000L, 200, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.ClaimCheck);

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeFalse();
        result.Error.Should().StartWith("DATA_SOURCE_UNAVAILABLE:");
        dataMock.Verify(d => d.IngestAsync(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
    }

    [Fact]
    public async Task Latest_window_downstream_failure_returns_service_failure()
    {
        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
                "BTCUSDT", "5", 300_000L, 200, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.Fail("DATA_SOURCE_UNAVAILABLE", "pg_42P01: relation missing"));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeFalse();
        result.Error.Should().Be("DATA_SOURCE_UNAVAILABLE: pg_42P01: relation missing");
    }

    [Fact]
    public async Task Ingest_error_cooldown_returns_service_busy_failure()
    {
        var dataMock = new Mock<IDataServiceClient>();
        var sut = CreateSut(cache: CacheWithIngestErrorCooldown(), data: dataMock.Object);

        var result = await sut.GetChartAsync("BTCUSDT", "5m", 200);

        result.IsSuccess.Should().BeFalse();
        result.Error.Should().StartWith("SERVICE_BUSY:");
        dataMock.Verify(d => d.GetLatestWindowRowsAsync(
            It.IsAny<string>(), It.IsAny<string>(), It.IsAny<long>(), It.IsAny<int>(),
            It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()), Times.Never);
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
    dataMock.Setup(d => d.GetLatestWindowRowsAsync(
        "BTCUSDT", "5", stepMs, limit, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsFetchResult.From(rows));
        dataMock.Setup(d => d.IngestAsync(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(IngestResult.Fail("not_needed"));

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
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
            "BTCUSDT", "5", stepMs, limit, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.From(rows));
        dataMock.Setup(d => d.IngestAsync("BTCUSDT", "5", It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(IngestResult.Ok("btcusdt_5", limit - available));
        dataMock.Setup(d => d.GetRowsAsync("btcusdt_5", It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<int>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.From(rows));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("partial");
        result.Value.Candles.Should().HaveCount(available);
        result.Value.RetryAfterMs.Should().BeGreaterThan(0);
        result.Value.Meta.Coverage.Should().Be("partial");
    }

    [Fact]
    public async Task Partial_data_is_rehydrated_synchronously_and_can_return_ok()
    {
        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var stepMs = 300_000L;
        var limit = 200;
        var available = 80;

        var coverage = new CoverageResult(true, "btcusdt_5", available,
            nowMs - (long)(available - 1) * stepMs, nowMs, 0.40);
        var partialRows = BuildRows(nowMs, available, stepMs);
        var fullRows = BuildRows(nowMs, limit, stepMs);

        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
            "BTCUSDT", "5", stepMs, limit, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.From(partialRows));
        dataMock.SetupSequence(d => d.GetRowsAsync("btcusdt_5", It.IsAny<long>(), It.IsAny<long>(),
            It.IsAny<int>(), It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsFetchResult.From(partialRows))
                .ReturnsAsync(RowsFetchResult.From(fullRows));
        dataMock.Setup(d => d.IngestAsync("BTCUSDT", "5", It.IsAny<long>(), It.IsAny<long>(),
                It.IsAny<CancellationToken>()))
                .ReturnsAsync(IngestResult.Ok("btcusdt_5", limit - available));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("ok");
        result.Value.Candles.Should().HaveCount(limit);
        result.Value.RetryAfterMs.Should().BeNull();
        result.Value.Meta.Coverage.Should().Be("full");
    }

    [Fact]
    public async Task Partial_data_stays_partial_when_sync_ingest_is_still_running()
    {
        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var stepMs = 300_000L;
        var limit = 200;
        var available = 80;
        var partialRows = BuildRows(nowMs, available, stepMs);

        var dataMock = new Mock<IDataServiceClient>();
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
            "BTCUSDT", "5", stepMs, limit, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(RowsFetchResult.From(partialRows));
        dataMock.Setup(d => d.IngestAsync(
                "BTCUSDT", "5", It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(IngestResult.InProgress(errorDetail: "ingest job 42 is still running"));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Status.Should().Be("partial");
        result.Value.Candles.Should().HaveCount(available);
        result.Value.RetryAfterMs.Should().BeGreaterThan(0);
        dataMock.Verify(d => d.GetRowsAsync(
            It.IsAny<string>(), It.IsAny<long>(), It.IsAny<long>(), It.IsAny<int>(),
            It.IsAny<IReadOnlyList<string>?>(), It.IsAny<CancellationToken>()), Times.Never);
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
        dataMock.Setup(d => d.GetLatestWindowRowsAsync(
            "BTCUSDT", "5", stepMs, limit, It.IsAny<IReadOnlyList<string>?>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
                .ReturnsAsync(RowsFetchResult.From(rows));
        dataMock.Setup(d => d.IngestAsync(It.IsAny<string>(), It.IsAny<string>(),
            It.IsAny<long>(), It.IsAny<long>(), It.IsAny<string>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(IngestResult.Fail("not_needed"));

        var sut = CreateSut(data: dataMock.Object);
        var result = await sut.GetChartAsync("BTCUSDT", "5m", limit);

        result.IsSuccess.Should().BeTrue();
        result.Value!.Meta.FromMs.Should().BeLessThan(result.Value.Meta.ToMs);
        result.Value.Meta.ToMs.Should().Be(rows[^1].TimestampMs);
        result.Value.Meta.Requested.Should().Be(limit);
        result.Value.Meta.Available.Should().Be(rows.Count);
    }
}
