using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Clients.Market;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using System.Globalization;

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
            services.RemoveAll<IMarketServiceClient>();

            services.AddSingleton<IMarketCacheService, NoopMarketCacheService>();
            services.AddSingleton<IBybitSymbolProvider>(
                new FakeBybitSymbolProvider(["BTCUSDT", "ETHUSDT", "SOLUSDT"]));
            services.AddSingleton<IMarketConfigService, FakeMarketConfigService>();
            services.AddSingleton<IChartService, FakeChartService>();
            services.AddSingleton<IMarketServiceClient, FakeMarketServiceClient>();
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
    private static readonly long StableNowMs = DateTimeOffset.Parse("2026-05-24T03:45:00Z").ToUnixTimeMilliseconds();

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

        var now    = StableNowMs;
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

internal sealed class FakeMarketServiceClient : IMarketServiceClient
{
    private static readonly DateTimeOffset UpdatedAt = DateTimeOffset.Parse("2026-05-24T03:45:00Z");
    private static readonly DateTimeOffset RealtimeUpdatedAt = DateTimeOffset.Parse("2026-05-24T03:45:12Z");

    private static readonly MarketTickerItemDto[] Items =
    [
        new()
        {
            Symbol = "BTCUSDT",
            DisplayName = "BTC / USDT",
            BaseAsset = "BTC",
            QuoteAsset = "USDT",
            Price = 106500m,
            Change24h = 2.5m,
            Volume24h = 1250000000m,
            MarketCap = 820000000m,
            High24h = 107200m,
            Low24h = 103800m,
            Rank = 1,
            LogoUrl = "https://cdn.test/btc.svg",
            ExchangeCount = 1,
            UpdatedAt = UpdatedAt,
            IsTrending = true,
        },
        new()
        {
            Symbol = "ETHUSDT",
            DisplayName = "ETH / USDT",
            BaseAsset = "ETH",
            QuoteAsset = "USDT",
            Price = 4250m,
            Change24h = 1.1m,
            Volume24h = 780000000m,
            MarketCap = 410000000m,
            High24h = 4295m,
            Low24h = 4180m,
            Rank = 2,
            LogoUrl = "https://cdn.test/eth.svg",
            ExchangeCount = 1,
            UpdatedAt = UpdatedAt,
            IsTrending = true,
        },
        new()
        {
            Symbol = "SOLUSDT",
            DisplayName = "SOL / USDT",
            BaseAsset = "SOL",
            QuoteAsset = "USDT",
            Price = 210m,
            Change24h = -0.8m,
            Volume24h = 315000000m,
            MarketCap = 180000000m,
            High24h = 215m,
            Low24h = 205m,
            Rank = 3,
            LogoUrl = "https://cdn.test/sol.svg",
            ExchangeCount = 1,
            UpdatedAt = UpdatedAt,
            IsTrending = false,
        }
    ];

    public Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default)
    {
        return Task.FromResult(ServiceResult<MarketOverviewDto>.Ok(new MarketOverviewDto
        {
            BtcDominance = 58.164729m,
            TotalMarketCapUsd = 1410000000m,
            Volume24hUsd = 2345000000m,
        }));
    }

    public Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default)
    {
        var items = Items.Take(limit).Select(item => new TrendingAssetDto
        {
            Symbol = item.Symbol,
            PriceUsd = item.Price,
            ChangePercent24h = item.Change24h,
        }).ToArray();
        return Task.FromResult(ServiceResult<IReadOnlyList<TrendingAssetDto>>.Ok(items));
    }

    public Task<ServiceResult<PublicMarketOverviewResponse>> GetPublicOverviewAsync(int trendingLimit = 5, CancellationToken ct = default)
    {
        return Task.FromResult(ServiceResult<PublicMarketOverviewResponse>.Ok(new PublicMarketOverviewResponse
        {
            MarketOverview = new PublicMarketOverviewDto
            {
                TotalMarketCap = 1410000000m,
                BtcDominance = 58.164729m,
                Volume24h = 2345000000m,
                ActiveAssets = Items.Length,
                FearGreedValue = 61,
                FearGreedLabel = "Greed",
            },
            TrendingAssets = Items.Take(trendingLimit).Select(item => item.Symbol).ToArray(),
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = UpdatedAt,
                UpdatedAt = UpdatedAt,
            }
        }));
    }

    public Task<ServiceResult<MarketTickersResponse>> GetTickersAsync(int page = 1, int pageSize = 25, string? search = null, string? sortBy = null, string? sortDir = null, IReadOnlyList<string>? symbols = null, string? collection = null, CancellationToken ct = default)
    {
        var normalizedCollection = collection?.Trim().ToLowerInvariant() switch
        {
            "trending" => "trending",
            "top-movers" => "top-movers",
            _ => "market"
        };

        IEnumerable<MarketTickerItemDto> items = Items;
        if (!string.IsNullOrWhiteSpace(search))
        {
            items = items.Where(item => item.Symbol.Contains(search, StringComparison.OrdinalIgnoreCase));
        }

        if (symbols is { Count: > 0 })
        {
            var filter = symbols.ToHashSet(StringComparer.OrdinalIgnoreCase);
            items = items.Where(item => filter.Contains(item.Symbol));
        }

        items = normalizedCollection switch
        {
            "trending" => items.Where(item => item.IsTrending).OrderByDescending(item => item.Change24h).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            "top-movers" => items.OrderByDescending(item => Math.Abs(item.Change24h)).ThenByDescending(item => item.Change24h).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            _ => items.OrderBy(item => item.Rank)
        };

        var pageItems = items.Take(pageSize).ToArray();
        return Task.FromResult(ServiceResult<MarketTickersResponse>.Ok(new MarketTickersResponse
        {
            SnapshotId = UpdatedAt.ToUnixTimeMilliseconds().ToString(CultureInfo.InvariantCulture),
            Collection = normalizedCollection,
            Items = pageItems,
            Total = pageItems.Length,
            Page = page,
            PageSize = pageSize,
            Search = search,
            SortBy = sortBy ?? "rank",
            SortDir = sortDir ?? "desc",
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = UpdatedAt,
                UpdatedAt = UpdatedAt,
            }
        }));
    }

    public Task<ServiceResult<MarketBatchQuotesResponse>> GetQuotesAsync(IReadOnlyList<string> symbols, CancellationToken ct = default)
    {
        var filter = (symbols ?? []).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var items = Items.Where(item => filter.Contains(item.Symbol)).Select(item => new MarketQuoteDto
        {
            Symbol = item.Symbol,
            Price = item.Price,
            Change24h = item.Change24h,
            High24h = item.High24h,
            Low24h = item.Low24h,
            Volume24h = item.Volume24h,
            UpdatedAt = item.UpdatedAt,
        }).ToArray();

        return Task.FromResult(ServiceResult<MarketBatchQuotesResponse>.Ok(new MarketBatchQuotesResponse
        {
            SnapshotId = UpdatedAt.ToUnixTimeMilliseconds().ToString(CultureInfo.InvariantCulture),
            Items = items,
            MissingSymbols = [],
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = UpdatedAt,
                UpdatedAt = UpdatedAt,
            }
        }));
    }

    public Task<ServiceResult<MarketRealtimeQuotesResponse>> GetRealtimeQuotesAsync(IReadOnlyList<string> symbols, string? exchange = null, CancellationToken ct = default)
    {
        var requestedSymbols = (symbols ?? [])
            .Where(static value => !string.IsNullOrWhiteSpace(value))
            .Select(static value => value.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requestedSymbols.Length == 0)
        {
            return Task.FromResult(ServiceResult<MarketRealtimeQuotesResponse>.Fail("At least one symbol is required"));
        }

        var normalizedExchange = string.IsNullOrWhiteSpace(exchange)
            ? null
            : exchange.Trim().ToLowerInvariant();

        var items = new List<MarketRealtimeQuoteDto>(requestedSymbols.Length);
        foreach (var symbol in requestedSymbols)
        {
            var snapshot = Items.FirstOrDefault(item => string.Equals(item.Symbol, symbol, StringComparison.OrdinalIgnoreCase));
            if (snapshot is null)
            {
                continue;
            }

            if (string.Equals(symbol, "BTCUSDT", StringComparison.OrdinalIgnoreCase)
                && (normalizedExchange is null || normalizedExchange == "bybit"))
            {
                items.Add(new MarketRealtimeQuoteDto
                {
                    Symbol = symbol,
                    Price = 106712.25m,
                    Change24h = snapshot.Change24h,
                    High24h = snapshot.High24h,
                    Low24h = snapshot.Low24h,
                    Volume24h = snapshot.Volume24h,
                    Exchange = "bybit",
                    RealtimeSymbol = symbol,
                    LagMs = 250,
                    Source = "market-watch-live",
                    IsFallback = false,
                    UpdatedAt = RealtimeUpdatedAt,
                });
                continue;
            }

            if (string.Equals(symbol, "ETHUSDT", StringComparison.OrdinalIgnoreCase)
                && (normalizedExchange is null || normalizedExchange == "bybit"))
            {
                items.Add(new MarketRealtimeQuoteDto
                {
                    Symbol = symbol,
                    Price = 4261.75m,
                    Change24h = snapshot.Change24h,
                    High24h = snapshot.High24h,
                    Low24h = snapshot.Low24h,
                    Volume24h = snapshot.Volume24h,
                    Exchange = "bybit",
                    RealtimeSymbol = symbol,
                    LagMs = 400,
                    Source = "market-watch-live",
                    IsFallback = false,
                    UpdatedAt = RealtimeUpdatedAt,
                });
                continue;
            }

            items.Add(new MarketRealtimeQuoteDto
            {
                Symbol = snapshot.Symbol,
                Price = snapshot.Price,
                Change24h = snapshot.Change24h,
                High24h = snapshot.High24h,
                Low24h = snapshot.Low24h,
                Volume24h = snapshot.Volume24h,
                Source = "snapshot-fallback",
                IsFallback = true,
                UpdatedAt = snapshot.UpdatedAt,
            });
        }

        var missing = requestedSymbols
            .Where(symbolValue => items.All(item => !string.Equals(item.Symbol, symbolValue, StringComparison.OrdinalIgnoreCase)))
            .ToArray();

        return Task.FromResult(ServiceResult<MarketRealtimeQuotesResponse>.Ok(new MarketRealtimeQuotesResponse
        {
            Items = items,
            MissingSymbols = missing,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = RealtimeUpdatedAt,
                UpdatedAt = items.Count > 0 ? items.Max(static item => item.UpdatedAt) : UpdatedAt,
                DegradedFields = items.Any(static item => item.IsFallback) ? ["realtimePrice"] : [],
            }
        }));
    }

    public Task<ServiceResult<MarketConverterQuoteResponse>> GetConverterQuoteAsync(string fromAsset, string toAsset, decimal amount, CancellationToken ct = default)
    {
        return Task.FromResult(ServiceResult<MarketConverterQuoteResponse>.Ok(new MarketConverterQuoteResponse
        {
            FromAsset = fromAsset.ToUpperInvariant(),
            ToAsset = toAsset.ToUpperInvariant(),
            Amount = amount,
            Rate = 25.058823m,
            ConvertedAmount = decimal.Round(amount * 25.058823m, 6, MidpointRounding.AwayFromZero),
            Source = "fake-market-service",
            GeneratedAt = UpdatedAt,
            UpdatedAt = UpdatedAt,
        }));
    }
}
