using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Market;
using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Clients.Market;

public sealed class MarketServiceClient : IMarketServiceClient
{
    private const string SnapshotCacheKey = "market:snapshot:linear:v1";
    private const string SnapshotSource = "bybit-linear-tickers";

    private readonly IMarketConfigService _marketConfig;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IMarketCacheService _cache;
    private readonly MarketSettings _settings;
    private readonly ILogger<MarketServiceClient> _logger;

    public MarketServiceClient(
        IMarketConfigService marketConfig,
        IHttpClientFactory httpClientFactory,
        IMarketCacheService cache,
        IOptions<MarketSettings> settings,
        ILogger<MarketServiceClient> logger)
    {
        _marketConfig = marketConfig;
        _httpClientFactory = httpClientFactory;
        _cache = cache;
        _settings = settings.Value;
        _logger = logger;
    }

    public async Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default)
    {
        var snapshot = await LoadSnapshotAsync(ct);
        return ServiceResult<MarketOverviewDto>.Ok(new MarketOverviewDto
        {
            BtcDominance = snapshot.TotalMarketCapProxy > 0
                ? DecimalRound(snapshot.BtcMarketCapProxy / snapshot.TotalMarketCapProxy * 100m)
                : 0,
            TotalMarketCapUsd = DecimalRound(snapshot.TotalMarketCapProxy),
            Volume24hUsd = DecimalRound(snapshot.TotalVolume24hUsd),
        });
    }

    public async Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default)
    {
        var snapshot = await LoadSnapshotAsync(ct);
        var items = snapshot.Items
            .OrderByDescending(item => item.TrendingScore)
            .ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .Take(Math.Max(1, limit))
            .Select(item => new TrendingAssetDto
            {
                Symbol = item.Symbol,
                PriceUsd = item.Price,
                ChangePercent24h = item.Change24h,
            })
            .ToArray();

        return ServiceResult<IReadOnlyList<TrendingAssetDto>>.Ok(items);
    }

    public async Task<ServiceResult<PublicMarketOverviewResponse>> GetPublicOverviewAsync(int trendingLimit = 5, CancellationToken ct = default)
    {
        var config = await _marketConfig.GetConfigAsync(ct);
        var snapshot = await LoadSnapshotAsync(ct);
        var (fearGreedValue, fearGreedLabel, fearGreedDegraded) = ComputeFearGreed(snapshot.Items);
        var totalMarketCap = snapshot.TotalMarketCapProxy > 0
            ? DecimalRound(snapshot.TotalMarketCapProxy)
            : (decimal?)null;
        var totalVolume24h = snapshot.TotalVolume24hUsd > 0
            ? DecimalRound(snapshot.TotalVolume24hUsd)
            : (decimal?)null;
        var btcDominance = totalMarketCap is > 0 && snapshot.BtcMarketCapProxy > 0
            ? DecimalRound(snapshot.BtcMarketCapProxy / snapshot.TotalMarketCapProxy * 100m)
            : (decimal?)null;

        var degradedFields = new HashSet<string>(snapshot.DegradedFields, StringComparer.OrdinalIgnoreCase);
        if (totalMarketCap is null)
        {
            degradedFields.Add("totalMarketCap");
        }

        if (totalVolume24h is null)
        {
            degradedFields.Add("volume24h");
        }

        if (btcDominance is null)
        {
            degradedFields.Add("btcDominance");
        }

        if (fearGreedDegraded)
        {
            degradedFields.Add("fearGreed");
        }

        return ServiceResult<PublicMarketOverviewResponse>.Ok(new PublicMarketOverviewResponse
        {
            MarketOverview = new PublicMarketOverviewDto
            {
                TotalMarketCap = totalMarketCap,
                BtcDominance = btcDominance,
                Volume24h = totalVolume24h,
                ActiveAssets = config.Symbols.Count,
                FearGreedValue = fearGreedDegraded ? null : fearGreedValue,
                FearGreedLabel = fearGreedDegraded ? null : fearGreedLabel,
            },
            TrendingAssets = snapshot.Items
                .OrderByDescending(item => item.TrendingScore)
                .ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
                .Take(Math.Max(1, trendingLimit))
                .Select(item => item.Symbol)
                .ToArray(),
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = snapshot.UpdatedAt,
                DegradedFields = degradedFields.ToArray(),
                DegradedSections = degradedFields.Count == 0 ? [] : ["marketOverview"],
            }
        });
    }

    public async Task<ServiceResult<MarketTickersResponse>> GetTickersAsync(
        int page = 1,
        int pageSize = 25,
        string? search = null,
        string? sortBy = null,
        string? sortDir = null,
        IReadOnlyList<string>? symbols = null,
        string? collection = null,
        CancellationToken ct = default)
    {
        var snapshot = await LoadSnapshotAsync(ct);
        var normalizedCollection = NormalizeCollection(collection);

        page = Math.Max(1, page);
        pageSize = Math.Clamp(pageSize, 1, 100);

        IEnumerable<SnapshotTicker> filtered = snapshot.Items;

        if (symbols is { Count: > 0 })
        {
            var filter = symbols
                .Where(static item => !string.IsNullOrWhiteSpace(item))
                .Select(static item => item.Trim().ToUpperInvariant())
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            filtered = filtered.Where(item => filter.Contains(item.Symbol));
        }

        if (!string.IsNullOrWhiteSpace(search))
        {
            var term = search.Trim();
            filtered = filtered.Where(item =>
                item.Symbol.Contains(term, StringComparison.OrdinalIgnoreCase)
                || item.DisplayName.Contains(term, StringComparison.OrdinalIgnoreCase)
                || item.BaseAsset.Contains(term, StringComparison.OrdinalIgnoreCase)
                || item.QuoteAsset.Contains(term, StringComparison.OrdinalIgnoreCase));
        }

        filtered = ApplyCollection(filtered, normalizedCollection);

        sortBy = ResolveSortBy(sortBy, normalizedCollection);
        sortDir = ResolveSortDir(sortDir, sortBy);

        var ordered = OrderTickers(filtered, sortBy, sortDir).ToArray();
        var total = ordered.Length;
        var pageItems = ordered
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .Select(ToTickerItemDto)
            .ToArray();

        return ServiceResult<MarketTickersResponse>.Ok(new MarketTickersResponse
        {
            SnapshotId = BuildSnapshotId(snapshot.UpdatedAt),
            Collection = normalizedCollection,
            Items = pageItems,
            Total = total,
            Page = page,
            PageSize = pageSize,
            Search = string.IsNullOrWhiteSpace(search) ? null : search.Trim(),
            SortBy = sortBy,
            SortDir = sortDir,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = snapshot.UpdatedAt,
                DegradedFields = snapshot.DegradedFields,
            }
        });
    }

    public async Task<ServiceResult<MarketBatchQuotesResponse>> GetQuotesAsync(IReadOnlyList<string> symbols, CancellationToken ct = default)
    {
        var requestedSymbols = (symbols ?? Array.Empty<string>())
            .Where(static item => !string.IsNullOrWhiteSpace(item))
            .Select(static item => item.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requestedSymbols.Length == 0)
        {
            return ServiceResult<MarketBatchQuotesResponse>.Fail("At least one symbol is required");
        }

        var snapshot = await LoadSnapshotAsync(ct);
        var lookup = snapshot.Items.ToDictionary(item => item.Symbol, StringComparer.OrdinalIgnoreCase);

        var items = new List<MarketQuoteDto>(requestedSymbols.Length);
        var missing = new List<string>();
        foreach (var symbol in requestedSymbols)
        {
            if (!lookup.TryGetValue(symbol, out var item))
            {
                missing.Add(symbol);
                continue;
            }

            items.Add(new MarketQuoteDto
            {
                Symbol = item.Symbol,
                Price = DecimalRound(item.Price),
                Change24h = DecimalRound(item.Change24h),
                High24h = DecimalRound(item.High24h),
                Low24h = DecimalRound(item.Low24h),
                Volume24h = DecimalRound(item.Volume24h),
                UpdatedAt = snapshot.UpdatedAt,
            });
        }

        return ServiceResult<MarketBatchQuotesResponse>.Ok(new MarketBatchQuotesResponse
        {
            SnapshotId = BuildSnapshotId(snapshot.UpdatedAt),
            Items = items,
            MissingSymbols = missing,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = snapshot.UpdatedAt,
                DegradedFields = snapshot.DegradedFields,
            }
        });
    }

    public async Task<ServiceResult<MarketConverterQuoteResponse>> GetConverterQuoteAsync(string fromAsset, string toAsset, decimal amount, CancellationToken ct = default)
    {
        var normalizedFrom = NormalizeAsset(fromAsset);
        var normalizedTo = NormalizeAsset(toAsset);
        if (string.IsNullOrWhiteSpace(normalizedFrom) || string.IsNullOrWhiteSpace(normalizedTo))
        {
            return ServiceResult<MarketConverterQuoteResponse>.Fail("Both fromAsset and toAsset are required");
        }

        if (amount <= 0)
        {
            return ServiceResult<MarketConverterQuoteResponse>.Fail("Amount must be greater than zero");
        }

        var snapshot = await LoadSnapshotAsync(ct);
        var fromPrice = ResolveUsdPrice(snapshot.Items, normalizedFrom);
        var toPrice = ResolveUsdPrice(snapshot.Items, normalizedTo);
        if (fromPrice is null || toPrice is null || toPrice.Value <= 0)
        {
            return ServiceResult<MarketConverterQuoteResponse>.Fail("Unsupported asset pair for converter quote");
        }

        var rate = fromPrice.Value / toPrice.Value;
        return ServiceResult<MarketConverterQuoteResponse>.Ok(new MarketConverterQuoteResponse
        {
            FromAsset = normalizedFrom,
            ToAsset = normalizedTo,
            Amount = amount,
            Rate = DecimalRound(rate),
            ConvertedAmount = DecimalRound(amount * rate),
            Source = SnapshotSource,
            GeneratedAt = DateTimeOffset.UtcNow,
            UpdatedAt = snapshot.UpdatedAt,
        });
    }

    private async Task<SnapshotEnvelope> LoadSnapshotAsync(CancellationToken ct)
    {
        return await _cache.GetOrCreateAsync(
            SnapshotCacheKey,
            TimeSpan.FromSeconds(Math.Max(1, _settings.SnapshotCacheTtlSeconds)),
            async () =>
            {
                var config = await _marketConfig.GetConfigAsync(ct);
                return await FetchSnapshotAsync(config.Symbols, ct);
            },
            ct);
    }

    private async Task<SnapshotEnvelope> FetchSnapshotAsync(IReadOnlyList<string> activeSymbols, CancellationToken ct)
    {
        var activeSet = activeSymbols
            .Where(static item => !string.IsNullOrWhiteSpace(item))
            .Select(static item => item.Trim().ToUpperInvariant())
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        var url = $"{_settings.BybitBaseUrl}/v5/market/tickers?category=linear";
        try
        {
            using var http = _httpClientFactory.CreateClient(nameof(MarketServiceClient));
            using var response = await http.GetAsync(url, ct);
            response.EnsureSuccessStatusCode();

            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
            if (!doc.RootElement.TryGetProperty("retCode", out var retCodeEl) || retCodeEl.GetInt32() != 0)
            {
                throw new InvalidOperationException("Bybit tickers endpoint returned non-zero retCode");
            }

            if (!doc.RootElement.TryGetProperty("result", out var resultEl)
                || !resultEl.TryGetProperty("list", out var listEl))
            {
                throw new InvalidOperationException("Bybit tickers endpoint returned no result.list");
            }

            var updatedAt = DateTimeOffset.UtcNow;
            var tickers = new Dictionary<string, SnapshotTicker>(StringComparer.OrdinalIgnoreCase);
            foreach (var item in listEl.EnumerateArray())
            {
                var symbol = GetString(item, "symbol")?.Trim().ToUpperInvariant();
                if (string.IsNullOrWhiteSpace(symbol) || !activeSet.Contains(symbol))
                {
                    continue;
                }

                tickers[symbol] = BuildTicker(symbol, item, updatedAt);
            }

            var items = activeSymbols
                .Select(symbol => tickers.TryGetValue(symbol, out var ticker)
                    ? ticker
                    : BuildFallbackTicker(symbol, updatedAt))
                .ToArray();

            var degradedFields = BuildDegradedFields(items, allFallback: false);
            return FinalizeSnapshot(items, updatedAt, degradedFields);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Market snapshot fetch failed; falling back to gateway-local empty snapshot");
            var updatedAt = DateTimeOffset.UtcNow;
            var fallback = activeSymbols.Select(symbol => BuildFallbackTicker(symbol, updatedAt)).ToArray();
            return FinalizeSnapshot(fallback, updatedAt, BuildDegradedFields(fallback, allFallback: true));
        }
    }

    private static SnapshotEnvelope FinalizeSnapshot(IReadOnlyList<SnapshotTicker> items, DateTimeOffset updatedAt, IReadOnlyList<string> degradedFields)
    {
        var ranked = items
            .OrderByDescending(item => item.MarketCapProxy)
            .ThenByDescending(item => item.Volume24h)
            .ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .Select((item, index) => item with { Rank = index + 1 })
            .ToArray();

        var trendingSet = ranked
            .OrderByDescending(item => item.TrendingScore)
            .ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .Take(10)
            .Select(item => item.Symbol)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        ranked = ranked.Select(item => item with { IsTrending = trendingSet.Contains(item.Symbol) }).ToArray();

        return new SnapshotEnvelope(
            ranked,
            updatedAt,
            DecimalRound(ranked.Sum(item => item.Volume24h)),
            DecimalRound(ranked.Sum(item => item.MarketCapProxy)),
            DecimalRound(ranked.FirstOrDefault(item => string.Equals(item.Symbol, "BTCUSDT", StringComparison.OrdinalIgnoreCase))?.MarketCapProxy ?? 0),
            degradedFields);
    }

    private static SnapshotTicker BuildTicker(string symbol, JsonElement item, DateTimeOffset updatedAt)
    {
        var baseAsset = ExtractBaseAsset(symbol);
        var quoteAsset = ExtractQuoteAsset(symbol);
        var price = GetDecimal(item, "lastPrice");
        var high24h = GetDecimal(item, "highPrice24h");
        var low24h = GetDecimal(item, "lowPrice24h");
        var price24hPcnt = GetDecimal(item, "price24hPcnt") * 100m;
        var turnover24h = GetDecimal(item, "turnover24h");
        var marketCapProxy = GetDecimal(item, "openInterestValue");
        if (marketCapProxy <= 0)
        {
            marketCapProxy = turnover24h;
        }

        return new SnapshotTicker(
            Symbol: symbol,
            DisplayName: $"{baseAsset} / {quoteAsset}",
            BaseAsset: baseAsset,
            QuoteAsset: quoteAsset,
            Price: DecimalRound(price),
            Change24h: DecimalRound(price24hPcnt),
            Volume24h: DecimalRound(turnover24h),
            MarketCapProxy: DecimalRound(marketCapProxy),
            High24h: DecimalRound(high24h),
            Low24h: DecimalRound(low24h),
            Rank: 0,
            LogoUrl: BuildLogoUrl(baseAsset),
            ExchangeCount: 1,
            UpdatedAt: updatedAt,
            IsTrending: false,
            TrendingScore: ComputeTrendingScore(price24hPcnt, turnover24h));
    }

    private static SnapshotTicker BuildFallbackTicker(string symbol, DateTimeOffset updatedAt)
    {
        var baseAsset = ExtractBaseAsset(symbol);
        var quoteAsset = ExtractQuoteAsset(symbol);
        return new SnapshotTicker(
            Symbol: symbol,
            DisplayName: $"{baseAsset} / {quoteAsset}",
            BaseAsset: baseAsset,
            QuoteAsset: quoteAsset,
            Price: 0,
            Change24h: 0,
            Volume24h: 0,
            MarketCapProxy: 0,
            High24h: 0,
            Low24h: 0,
            Rank: 0,
            LogoUrl: BuildLogoUrl(baseAsset),
            ExchangeCount: 1,
            UpdatedAt: updatedAt,
            IsTrending: false,
            TrendingScore: 0);
    }

    private static IReadOnlyList<string> BuildDegradedFields(IReadOnlyList<SnapshotTicker> items, bool allFallback)
    {
        var degraded = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (allFallback)
        {
            degraded.UnionWith(["price", "change24h", "volume24h", "marketCap", "high24h", "low24h"]);
            return degraded.ToArray();
        }

        if (items.Any(item => item.Price <= 0)) degraded.Add("price");
        if (items.Any(item => item.Change24h == 0)) degraded.Add("change24h");
        if (items.Any(item => item.Volume24h <= 0)) degraded.Add("volume24h");
        if (items.Any(item => item.MarketCapProxy <= 0)) degraded.Add("marketCap");
        if (items.Any(item => item.High24h <= 0)) degraded.Add("high24h");
        if (items.Any(item => item.Low24h <= 0)) degraded.Add("low24h");
        return degraded.ToArray();
    }

    private static decimal? ResolveUsdPrice(IReadOnlyList<SnapshotTicker> items, string asset)
    {
        if (string.Equals(asset, "USDT", StringComparison.OrdinalIgnoreCase))
        {
            return 1m;
        }

        var symbol = $"{asset}USDT";
        return items.FirstOrDefault(item => string.Equals(item.Symbol, symbol, StringComparison.OrdinalIgnoreCase))?.Price;
    }

    private static IEnumerable<SnapshotTicker> ApplyCollection(IEnumerable<SnapshotTicker> source, string collection)
    {
        return collection switch
        {
            "trending" => source.Where(item => item.IsTrending || item.TrendingScore > 0),
            "top-movers" => source.Where(item => item.Change24h != 0),
            _ => source,
        };
    }

    private static string NormalizeCollection(string? collection)
    {
        return collection?.Trim().ToLowerInvariant() switch
        {
            "trending" => "trending",
            "top-movers" => "top-movers",
            _ => "market"
        };
    }

    private static string ResolveSortBy(string? sortBy, string collection)
    {
        var value = sortBy?.Trim().ToLowerInvariant();
        return value switch
        {
            "symbol" or "displayname" or "price" or "change24h" or "volume24h" or "marketcap" or "high24h" or "low24h" or "rank" or "updatedat" => value,
            "trending" => "trending",
            "top-movers" or "topmovers" => "top-movers",
            _ => collection switch
            {
                "trending" => "trending",
                "top-movers" => "top-movers",
                _ => "rank"
            }
        };
    }

    private static string ResolveSortDir(string? sortDir, string sortBy)
    {
        if (!string.IsNullOrWhiteSpace(sortDir))
        {
            return string.Equals(sortDir, "asc", StringComparison.OrdinalIgnoreCase) ? "asc" : "desc";
        }

        return string.Equals(sortBy, "rank", StringComparison.OrdinalIgnoreCase) ? "asc" : "desc";
    }

    private static IOrderedEnumerable<SnapshotTicker> OrderTickers(IEnumerable<SnapshotTicker> source, string sortBy, string sortDir)
    {
        var descending = !string.Equals(sortDir, "asc", StringComparison.OrdinalIgnoreCase);
        return (sortBy, descending) switch
        {
            ("symbol", false) => source.OrderBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("symbol", true) => source.OrderByDescending(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("displayname", false) => source.OrderBy(item => item.DisplayName, StringComparer.OrdinalIgnoreCase),
            ("displayname", true) => source.OrderByDescending(item => item.DisplayName, StringComparer.OrdinalIgnoreCase),
            ("price", false) => source.OrderBy(item => item.Price),
            ("price", true) => source.OrderByDescending(item => item.Price),
            ("change24h", false) => source.OrderBy(item => item.Change24h),
            ("change24h", true) => source.OrderByDescending(item => item.Change24h),
            ("volume24h", false) => source.OrderBy(item => item.Volume24h),
            ("volume24h", true) => source.OrderByDescending(item => item.Volume24h),
            ("marketcap", false) => source.OrderBy(item => item.MarketCapProxy),
            ("marketcap", true) => source.OrderByDescending(item => item.MarketCapProxy),
            ("trending", false) => source.OrderBy(item => item.TrendingScore).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("trending", true) => source.OrderByDescending(item => item.TrendingScore).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("top-movers", false) => source.OrderBy(item => Math.Abs(item.Change24h)).ThenBy(item => item.Change24h).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("top-movers", true) => source.OrderByDescending(item => Math.Abs(item.Change24h)).ThenByDescending(item => item.Change24h).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("high24h", false) => source.OrderBy(item => item.High24h),
            ("high24h", true) => source.OrderByDescending(item => item.High24h),
            ("low24h", false) => source.OrderBy(item => item.Low24h),
            ("low24h", true) => source.OrderByDescending(item => item.Low24h),
            ("updatedat", false) => source.OrderBy(item => item.UpdatedAt),
            ("updatedat", true) => source.OrderByDescending(item => item.UpdatedAt),
            ("rank", false) => source.OrderBy(item => item.Rank),
            _ => source.OrderByDescending(item => item.Rank),
        };
    }

    private static string BuildSnapshotId(DateTimeOffset updatedAt)
    {
        return updatedAt.ToUnixTimeMilliseconds().ToString(CultureInfo.InvariantCulture);
    }

    private static MarketTickerItemDto ToTickerItemDto(SnapshotTicker item)
    {
        return new MarketTickerItemDto
        {
            Symbol = item.Symbol,
            DisplayName = item.DisplayName,
            BaseAsset = item.BaseAsset,
            QuoteAsset = item.QuoteAsset,
            Price = item.Price,
            Change24h = item.Change24h,
            Volume24h = item.Volume24h,
            MarketCap = item.MarketCapProxy > 0 ? item.MarketCapProxy : null,
            High24h = item.High24h,
            Low24h = item.Low24h,
            Rank = item.Rank,
            LogoUrl = item.LogoUrl,
            ExchangeCount = item.ExchangeCount,
            UpdatedAt = item.UpdatedAt,
            IsTrending = item.IsTrending,
        };
    }

    private static (int Value, string Label, bool Degraded) ComputeFearGreed(IReadOnlyList<SnapshotTicker> items)
    {
        var active = items.Where(item => item.Change24h != 0).ToArray();
        if (active.Length == 0)
        {
            return (0, "Neutral", true);
        }

        var positive = active.Count(item => item.Change24h > 0);
        var negative = active.Count(item => item.Change24h < 0);
        var breadth = (positive - negative) / (decimal)active.Length;
        var averageChange = active.Average(item => item.Change24h);
        var normalizedAverage = Math.Clamp((double)(averageChange / 10m), -1d, 1d);
        var score = (int)Math.Round(Math.Clamp(50d + (double)breadth * 25d + normalizedAverage * 25d, 0d, 100d));
        var label = score switch
        {
            <= 20 => "Extreme Fear",
            < 40 => "Fear",
            < 60 => "Neutral",
            < 80 => "Greed",
            _ => "Extreme Greed",
        };

        return (score, label, false);
    }

    private static decimal ComputeTrendingScore(decimal change24h, decimal volume24h)
    {
        var weightedMagnitude = Math.Abs(change24h);
        var liquidityBoost = (decimal)Math.Log10((double)Math.Max(volume24h, 1m));
        return DecimalRound(weightedMagnitude * Math.Max(liquidityBoost, 1m));
    }

    private static string? GetString(JsonElement item, string name)
    {
        return item.TryGetProperty(name, out var property)
            ? property.GetString()
            : null;
    }

    private static decimal GetDecimal(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return 0;
        }

        return property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetDecimal(out var numberValue) => numberValue,
            JsonValueKind.String when decimal.TryParse(property.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0,
        };
    }

    private static string NormalizeAsset(string asset) => asset?.Trim().ToUpperInvariant() ?? string.Empty;

    private static string ExtractBaseAsset(string symbol)
    {
        var normalized = NormalizeAsset(symbol);
        return normalized.EndsWith("USDT", StringComparison.OrdinalIgnoreCase) && normalized.Length > 4
            ? normalized[..^4]
            : normalized;
    }

    private static string ExtractQuoteAsset(string symbol)
    {
        var normalized = NormalizeAsset(symbol);
        return normalized.EndsWith("USDT", StringComparison.OrdinalIgnoreCase)
            ? "USDT"
            : string.Empty;
    }

    private static string? BuildLogoUrl(string baseAsset)
    {
        if (string.IsNullOrWhiteSpace(baseAsset))
        {
            return null;
        }

        return $"https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color/{baseAsset.ToLowerInvariant()}.svg";
    }

    private static decimal DecimalRound(decimal value) => decimal.Round(value, 6, MidpointRounding.AwayFromZero);

    private sealed record SnapshotEnvelope(
        IReadOnlyList<SnapshotTicker> Items,
        DateTimeOffset UpdatedAt,
        decimal TotalVolume24hUsd,
        decimal TotalMarketCapProxy,
        decimal BtcMarketCapProxy,
        IReadOnlyList<string> DegradedFields);

    private sealed record SnapshotTicker(
        string Symbol,
        string DisplayName,
        string BaseAsset,
        string QuoteAsset,
        decimal Price,
        decimal Change24h,
        decimal Volume24h,
        decimal MarketCapProxy,
        decimal High24h,
        decimal Low24h,
        int Rank,
        string? LogoUrl,
        int ExchangeCount,
        DateTimeOffset UpdatedAt,
        bool IsTrending,
        decimal TrendingScore);
}
