using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using FluentAssertions;
using Xunit;

namespace GatewayService.IntegrationTests;

/// <summary>
/// Integration tests for the Market API endpoints.
/// Uses <see cref="GatewayTestWebAppFactory"/> which replaces all real downstream
/// dependencies (Kafka, Redis, Bybit) with in-memory fakes.
/// </summary>
[Collection("Integration")]
public sealed class MarketIntegrationTests : IClassFixture<GatewayTestWebAppFactory>
{
    private readonly HttpClient _client;

    public MarketIntegrationTests(GatewayTestWebAppFactory factory)
    {
        _client = factory.CreateClient();
    }

    // ── GET /api/v1/market/config ─────────────────────────────────────────

    [Fact]
    public async Task Config_returns_200()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task Config_returns_json_content_type()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        response.Content.Headers.ContentType?.MediaType.Should().Be("application/json");
    }

    [Fact]
    public async Task Config_response_contains_symbols()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        body.GetProperty("symbols").GetArrayLength().Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task Config_response_has_eleven_timeframes()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        body.GetProperty("timeframes").GetArrayLength().Should().Be(11);
    }

    [Fact]
    public async Task Config_response_candleCounts_has_heavy_medium_light()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        var cc = body.GetProperty("candleCounts");
        cc.GetProperty("heavy").GetArrayLength().Should().BeGreaterThan(0);
        cc.GetProperty("medium").GetArrayLength().Should().BeGreaterThan(0);
        cc.GetProperty("light").GetArrayLength().Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task Config_response_has_defaults_section()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        var defaults = body.GetProperty("defaults");
        defaults.GetProperty("symbol").GetString().Should().NotBeNullOrEmpty();
        defaults.GetProperty("timeframe").GetString().Should().NotBeNullOrEmpty();
        defaults.GetProperty("candleCount").GetInt32().Should().BeGreaterThan(0);
    }

    [Fact]
    public async Task Config_cache_control_header_is_set()
    {
        var response = await _client.GetAsync("/api/v1/market/config");
        var cc       = response.Headers.CacheControl;

        cc.Should().NotBeNull();
        cc!.Public.Should().BeTrue();
    }

    // ── GET /api/v1/market/chart ──────────────────────────────────────────

    [Theory]
    [InlineData("FAKEUSDT", "5m",  200)]
    [InlineData("XYZUSDT",  "1m",  50)]
    public async Task Chart_unknown_symbol_returns_400(string symbol, string timeframe, int limit)
    {
        var url = $"/api/v1/market/chart?symbol={symbol}&timeframe={timeframe}&limit={limit}";
        var response = await _client.GetAsync(url);
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Theory]
    [InlineData("BTCUSDT", "99x",   200)]
    [InlineData("BTCUSDT", "daily", 200)]
    [InlineData("BTCUSDT", "1h",    200)]
    public async Task Chart_invalid_timeframe_returns_400(string symbol, string timeframe, int limit)
    {
        var url = $"/api/v1/market/chart?symbol={symbol}&timeframe={timeframe}&limit={limit}";
        var response = await _client.GetAsync(url);
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Theory]
    [InlineData("BTCUSDT", "5m",  2000)]   // 2000 not in Heavy grid
    [InlineData("BTCUSDT", "5m",  150)]    // 150 not in any grid
    [InlineData("BTCUSDT", "1m",  0)]      // 0 never valid
    public async Task Chart_invalid_limit_returns_400(string symbol, string timeframe, int limit)
    {
        var url = $"/api/v1/market/chart?symbol={symbol}&timeframe={timeframe}&limit={limit}";
        var response = await _client.GetAsync(url);
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Chart_valid_request_returns_200()
    {
        var response = await _client.GetAsync("/api/v1/market/chart?symbol=BTCUSDT&timeframe=5m&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }

    [Fact]
    public async Task Chart_valid_request_returns_candles()
    {
        var response = await _client.GetAsync("/api/v1/market/chart?symbol=BTCUSDT&timeframe=5m&limit=200");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        body.GetProperty("candles").GetArrayLength().Should().Be(200);
    }

    [Fact]
    public async Task Chart_candles_have_required_fields()
    {
        var response = await _client.GetAsync("/api/v1/market/chart?symbol=BTCUSDT&timeframe=5m&limit=50");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        var firstCandle = body.GetProperty("candles")[0];
        firstCandle.TryGetProperty("t", out _).Should().BeTrue();
        firstCandle.TryGetProperty("o", out _).Should().BeTrue();
        firstCandle.TryGetProperty("h", out _).Should().BeTrue();
        firstCandle.TryGetProperty("l", out _).Should().BeTrue();
        firstCandle.TryGetProperty("c", out _).Should().BeTrue();
        firstCandle.TryGetProperty("v", out _).Should().BeTrue();
    }

    [Fact]
    public async Task Chart_response_has_status_field()
    {
        var response = await _client.GetAsync("/api/v1/market/chart?symbol=BTCUSDT&timeframe=5m&limit=200");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        body.TryGetProperty("status", out var status).Should().BeTrue();
        status.GetString().Should().BeOneOf("ok", "partial", "pending");
    }

    [Fact]
    public async Task Chart_response_has_meta_section()
    {
        var response = await _client.GetAsync("/api/v1/market/chart?symbol=BTCUSDT&timeframe=5m&limit=200");
        var body     = await response.Content.ReadFromJsonAsync<JsonElement>();

        var meta = body.GetProperty("meta");
        meta.GetProperty("requested").GetInt32().Should().Be(200);
        meta.TryGetProperty("coverage", out _).Should().BeTrue();
    }

    [Fact]
    public async Task Chart_missing_symbol_returns_400()
    {
        var response = await _client.GetAsync("/api/v1/market/chart?timeframe=5m&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.BadRequest);
    }

    [Fact]
    public async Task Chart_case_insensitive_symbol_is_accepted()
    {
        // "btcusdt" (lower) should resolve the same as "BTCUSDT"
        var response = await _client.GetAsync("/api/v1/market/chart?symbol=btcusdt&timeframe=5m&limit=200");
        response.StatusCode.Should().Be(HttpStatusCode.OK);
    }
}
