using System.Net;
using System.Text;
using FluentAssertions;
using GatewayService.API.Clients.Market;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace GatewayService.UnitTests.Market;

public sealed class MarketServiceClientOverviewTests
{
    private readonly Mock<IMarketConfigService> _marketConfig = new();
    private readonly Mock<ICoinMetadataService> _coinMetadata = new();
    private readonly Mock<IKafkaRequestClient> _kafka = new();

    [Fact]
    public async Task GetPublicOverviewAsync_prefers_canonical_global_metrics_over_snapshot_proxies()
    {
        _marketConfig.Setup(service => service.GetConfigAsync(It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MarketConfigResponse
            {
                Symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            });

        using var httpFactory = new StubHttpClientFactory(request =>
        {
            var url = request.RequestUri!.ToString();
            if (url.Contains("api.coingecko.com/api/v3/global", StringComparison.Ordinal))
            {
                return Json(HttpStatusCode.OK, """
                {
                  "data": {
                    "active_cryptocurrencies": 17399,
                    "total_market_cap": { "usd": 2646464699116.617 },
                    "total_volume": { "usd": 105991000000.12 },
                    "market_cap_percentage": { "btc": 51.2345 },
                    "updated_at": 1779603021
                  }
                }
                """);
            }

            if (url.Contains("api.alternative.me/fng", StringComparison.Ordinal))
            {
                return Json(HttpStatusCode.OK, """
                {
                  "data": [
                    {
                      "value": "25",
                      "value_classification": "Extreme Fear",
                      "timestamp": "1779580800"
                    }
                  ],
                  "metadata": { "error": null }
                }
                """);
            }

            if (url.Contains("api.bybit.com/v5/market/tickers?category=linear", StringComparison.Ordinal))
            {
                return Json(HttpStatusCode.OK, """
                {
                  "retCode": 0,
                  "result": {
                    "list": [
                      {
                        "symbol": "BTCUSDT",
                        "lastPrice": "100000",
                        "highPrice24h": "101000",
                        "lowPrice24h": "99000",
                        "price24hPcnt": "0.02",
                        "turnover24h": "1500000000",
                        "openInterestValue": "500000000"
                      },
                      {
                        "symbol": "ETHUSDT",
                        "lastPrice": "4000",
                        "highPrice24h": "4050",
                        "lowPrice24h": "3900",
                        "price24hPcnt": "0.01",
                        "turnover24h": "800000000",
                        "openInterestValue": "200000000"
                      },
                      {
                        "symbol": "SOLUSDT",
                        "lastPrice": "200",
                        "highPrice24h": "205",
                        "lowPrice24h": "190",
                        "price24hPcnt": "0.03",
                        "turnover24h": "400000000",
                        "openInterestValue": "100000000"
                      }
                    ]
                  }
                }
                """);
            }

            throw new InvalidOperationException($"Unexpected request URL: {url}");
        });

        var sut = CreateSut(httpFactory);

        var result = await sut.GetPublicOverviewAsync();

        result.IsSuccess.Should().BeTrue();
        result.Value.Should().NotBeNull();
        result.Value!.MarketOverview.TotalMarketCap.Should().Be(2646464699116.617m);
        result.Value.MarketOverview.Volume24h.Should().Be(105991000000.12m);
        result.Value.MarketOverview.BtcDominance.Should().Be(51.2345m);
        result.Value.MarketOverview.ActiveAssets.Should().Be(17399);
        result.Value.MarketOverview.FearGreedValue.Should().Be(25);
        result.Value.MarketOverview.FearGreedLabel.Should().Be("Extreme Fear");
        result.Value.TrendingAssets.Should().ContainInOrder("SOLUSDT", "BTCUSDT", "ETHUSDT");
        result.Value.Meta.DegradedSections.Should().BeEmpty();
        result.Value.Meta.DegradedFields.Should().BeEmpty();
    }

    [Fact]
    public async Task GetPublicOverviewAsync_marks_market_overview_degraded_when_canonical_feeds_fail()
    {
        _marketConfig.Setup(service => service.GetConfigAsync(It.IsAny<string?>(), It.IsAny<CancellationToken>()))
            .ReturnsAsync(new MarketConfigResponse
            {
                Symbols = ["BTCUSDT", "ETHUSDT"],
            });

        using var httpFactory = new StubHttpClientFactory(request =>
        {
            var url = request.RequestUri!.ToString();
            if (url.Contains("api.bybit.com/v5/market/tickers?category=linear", StringComparison.Ordinal))
            {
                return Json(HttpStatusCode.OK, """
                {
                  "retCode": 0,
                  "result": {
                    "list": [
                      {
                        "symbol": "BTCUSDT",
                        "lastPrice": "100000",
                        "highPrice24h": "101000",
                        "lowPrice24h": "99000",
                        "price24hPcnt": "0.02",
                        "turnover24h": "1500000000",
                        "openInterestValue": "500000000"
                      },
                      {
                        "symbol": "ETHUSDT",
                        "lastPrice": "4000",
                        "highPrice24h": "4050",
                        "lowPrice24h": "3900",
                        "price24hPcnt": "0",
                        "turnover24h": "800000000",
                        "openInterestValue": "200000000"
                      }
                    ]
                  }
                }
                """);
            }

            return new HttpResponseMessage(HttpStatusCode.BadGateway)
            {
                RequestMessage = request,
                Content = new StringContent("upstream failed", Encoding.UTF8, "text/plain"),
            };
        });

        var sut = CreateSut(httpFactory);

        var result = await sut.GetPublicOverviewAsync();

        result.IsSuccess.Should().BeTrue();
        result.Value.Should().NotBeNull();
        result.Value!.MarketOverview.TotalMarketCap.Should().BeNull();
        result.Value.MarketOverview.Volume24h.Should().BeNull();
        result.Value.MarketOverview.BtcDominance.Should().BeNull();
        result.Value.MarketOverview.ActiveAssets.Should().BeNull();
        result.Value.MarketOverview.FearGreedValue.Should().BeNull();
        result.Value.MarketOverview.FearGreedLabel.Should().BeNull();
        result.Value.Meta.DegradedSections.Should().Contain(["marketOverview", "trendingAssets"]);
        result.Value.Meta.DegradedFields.Should().Contain([
            "totalMarketCap",
            "volume24h",
            "btcDominance",
            "activeAssets",
            "fearGreedValue",
            "fearGreedLabel",
            "trending.change24h"
        ]);
    }

    private MarketServiceClient CreateSut(IHttpClientFactory httpClientFactory)
    {
        // Positive circulating supply for the tracked bases so the snapshot can
        // compute a real (non-null) market cap and `marketCap` does NOT show up
        // as a degraded field. Coins with no live price (fallback tickers) still
        // yield a null cap regardless of this metadata.
        _coinMetadata.Setup(service => service.GetMetadataAsync(It.IsAny<CancellationToken>()))
            .ReturnsAsync(new Dictionary<string, CoinMetadata>(StringComparer.OrdinalIgnoreCase)
            {
                ["BTC"] = new(CirculatingSupply: 19_800_000m, TotalSupply: 19_800_000m, MaxSupply: 21_000_000m, Ath: 109_000m),
                ["ETH"] = new(CirculatingSupply: 120_000_000m, TotalSupply: 120_000_000m, MaxSupply: null, Ath: 4_900m),
                ["SOL"] = new(CirculatingSupply: 470_000_000m, TotalSupply: 590_000_000m, MaxSupply: null, Ath: 295m),
            });

        return new MarketServiceClient(
            _marketConfig.Object,
            _coinMetadata.Object,
            httpClientFactory,
            new PassthroughMarketCacheService(),
            _kafka.Object,
            Options.Create(new MarketSettings()),
            NullLogger<MarketServiceClient>.Instance);
    }

    private static HttpResponseMessage Json(HttpStatusCode statusCode, string body)
    {
        return new HttpResponseMessage(statusCode)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json"),
        };
    }

    private sealed class StubHttpClientFactory(Func<HttpRequestMessage, HttpResponseMessage> responder) : IHttpClientFactory, IDisposable
    {
        private readonly HttpClient _client = new(new StubHandler(responder), disposeHandler: true);

        public HttpClient CreateClient(string name) => _client;

        public void Dispose() => _client.Dispose();
    }

    private sealed class StubHandler(Func<HttpRequestMessage, HttpResponseMessage> responder) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            return Task.FromResult(responder(request));
        }
    }

    private sealed class PassthroughMarketCacheService : IMarketCacheService
    {
        private readonly Dictionary<string, object> _values = new(StringComparer.Ordinal);

        public Task<T?> GetAsync<T>(string key, CancellationToken ct = default) where T : class
        {
            return Task.FromResult(_values.TryGetValue(key, out var value) ? value as T : null);
        }

        public Task SetAsync<T>(string key, T value, TimeSpan ttl, CancellationToken ct = default) where T : class
        {
            _values[key] = value;
            return Task.CompletedTask;
        }

        public Task<bool> SetIfNotExistsAsync(string key, string value, TimeSpan ttl, CancellationToken ct = default)
        {
            if (_values.ContainsKey(key)) return Task.FromResult(false);
            _values[key] = value;
            return Task.FromResult(true);
        }

        public Task RemoveAsync(string key, CancellationToken ct = default)
        {
            _values.Remove(key);
            return Task.CompletedTask;
        }

        public async Task<T> GetOrCreateAsync<T>(string key, TimeSpan ttl, Func<Task<T>> factory, CancellationToken ct = default) where T : class
        {
            var cached = await GetAsync<T>(key, ct);
            if (cached is not null)
            {
                return cached;
            }

            var created = await factory();
            await SetAsync(key, created, ttl, ct);
            return created;
        }
    }
}
