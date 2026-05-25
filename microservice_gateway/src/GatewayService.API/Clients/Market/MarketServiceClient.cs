using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Clients.Market;

public sealed class MarketServiceClient : IMarketServiceClient
{
    private const string SnapshotCacheKey = "market:snapshot:linear:v1";
    private const string CanonicalOverviewCacheKey = "market:overview:canonical:v2";
    private const string SnapshotSource = "bybit-linear-tickers";
    private const string RealtimeSource = "market-watch-live";
    private const string SnapshotFallbackSource = "snapshot-fallback";
    private const int RealtimeRowsLimit = 500;

    private readonly IMarketConfigService _marketConfig;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IMarketCacheService _cache;
    private readonly IKafkaRequestClient _kafka;
    private readonly MarketSettings _settings;
    private readonly ILogger<MarketServiceClient> _logger;

    public MarketServiceClient(
        IMarketConfigService marketConfig,
        IHttpClientFactory httpClientFactory,
        IMarketCacheService cache,
        IKafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<MarketServiceClient> logger)
    {
        _marketConfig = marketConfig;
        _httpClientFactory = httpClientFactory;
        _cache = cache;
        _kafka = kafka;
        _settings = settings.Value;
        _logger = logger;
    }


public async Task<ServiceResult<MarketOverviewDto>> GetOverviewAsync(CancellationToken ct = default)
{
    var canonical = await LoadCanonicalOverviewAsync(ct);
    if (canonical.TotalMarketCapUsd is null || canonical.BtcDominance is null || canonical.Volume24hUsd is null)
    {
        return ServiceResult<MarketOverviewDto>.Fail("Canonical global market overview is unavailable");
    }

    return ServiceResult<MarketOverviewDto>.Ok(new MarketOverviewDto
    {
        BtcDominance = canonical.BtcDominance.Value,
        TotalMarketCapUsd = canonical.TotalMarketCapUsd.Value,
        Volume24hUsd = canonical.Volume24hUsd.Value,
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
    var snapshotTask = LoadSnapshotAsync(ct);
    var canonicalTask = LoadCanonicalOverviewAsync(ct);
    await Task.WhenAll(snapshotTask, canonicalTask);

    var snapshot = await snapshotTask;
    var canonical = await canonicalTask;

    var overviewDegradedFields = new HashSet<string>(canonical.DegradedFields, StringComparer.OrdinalIgnoreCase);
    var degradedFields = new HashSet<string>(overviewDegradedFields, StringComparer.OrdinalIgnoreCase);
    if (snapshot.DegradedFields.Count > 0)
    {
        degradedFields.UnionWith(snapshot.DegradedFields.Select(field => $"trending.{field}"));
    }

    var degradedSections = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    if (overviewDegradedFields.Count > 0)
    {
        degradedSections.Add("marketOverview");
    }

    if (snapshot.DegradedFields.Count > 0)
    {
        degradedSections.Add("trendingAssets");
    }

    return ServiceResult<PublicMarketOverviewResponse>.Ok(new PublicMarketOverviewResponse
    {
        MarketOverview = new PublicMarketOverviewDto
        {
            TotalMarketCap = canonical.TotalMarketCapUsd,
            BtcDominance = canonical.BtcDominance,
            Volume24h = canonical.Volume24hUsd,
            ActiveAssets = canonical.ActiveAssets,
            FearGreedValue = canonical.FearGreedValue,
            FearGreedLabel = canonical.FearGreedLabel,
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
            UpdatedAt = ResolveCompositeUpdatedAt(canonical.UpdatedAt, snapshot.UpdatedAt),
            DegradedFields = degradedFields.ToArray(),
            DegradedSections = degradedSections.ToArray(),
        }
    });
}

private async Task<CanonicalOverviewEnvelope> LoadCanonicalOverviewAsync(CancellationToken ct)
{
    return await _cache.GetOrCreateAsync(
        CanonicalOverviewCacheKey,
        TimeSpan.FromSeconds(Math.Max(30, _settings.GlobalOverviewCacheTtlSeconds)),
        async () =>
        {
            var marketTask = TryFetchCoinGeckoGlobalAsync(ct);
            var fearGreedTask = TryFetchFearGreedAsync(ct);
            await Task.WhenAll(marketTask, fearGreedTask);

            var market = await marketTask;
            var fearGreed = await fearGreedTask;
            var degradedFields = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            if (market?.TotalMarketCapUsd is null) degradedFields.Add("totalMarketCap");
            if (market?.Volume24hUsd is null) degradedFields.Add("volume24h");
            if (market?.BtcDominance is null) degradedFields.Add("btcDominance");
            if (market?.ActiveAssets is null) degradedFields.Add("activeAssets");
            if (fearGreed?.Value is null) degradedFields.Add("fearGreedValue");
            if (string.IsNullOrWhiteSpace(fearGreed?.Label)) degradedFields.Add("fearGreedLabel");

            return new CanonicalOverviewEnvelope(
                TotalMarketCapUsd: market?.TotalMarketCapUsd,
                BtcDominance: market?.BtcDominance,
                Volume24hUsd: market?.Volume24hUsd,
                ActiveAssets: market?.ActiveAssets,
                FearGreedValue: fearGreed?.Value,
                FearGreedLabel: fearGreed?.Label,
                UpdatedAt: ResolveCompositeUpdatedAt(market?.UpdatedAt, fearGreed?.UpdatedAt),
                DegradedFields: degradedFields.ToArray());
        },
        ct);
}

private async Task<CoinGeckoGlobalSnapshot?> TryFetchCoinGeckoGlobalAsync(CancellationToken ct)
{
    var url = $"{_settings.CoinGeckoBaseUrl.TrimEnd('/')}/global";
    try
    {
        using var http = _httpClientFactory.CreateClient(nameof(MarketServiceClient));
        using var response = await http.GetAsync(url, ct);
        response.EnsureSuccessStatusCode();

        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

        if (!doc.RootElement.TryGetProperty("data", out var data))
        {
            throw new InvalidOperationException("CoinGecko global endpoint returned no data payload");
        }

        var updatedAt = TryGetUnixSeconds(data, "updated_at") ?? DateTimeOffset.UtcNow;
        var totalMarketCap = TryGetNestedDecimal(data, "total_market_cap", "usd");
        var totalVolume24h = TryGetNestedDecimal(data, "total_volume", "usd");
        var btcDominance = TryGetNestedDecimal(data, "market_cap_percentage", "btc");
        var activeAssets = TryGetInt32(data, "active_cryptocurrencies");

        return new CoinGeckoGlobalSnapshot(
            TotalMarketCapUsd: totalMarketCap > 0 ? DecimalRound(totalMarketCap.Value) : null,
            BtcDominance: btcDominance >= 0 ? DecimalRound(btcDominance.Value) : null,
            Volume24hUsd: totalVolume24h > 0 ? DecimalRound(totalVolume24h.Value) : null,
            ActiveAssets: activeAssets > 0 ? activeAssets : null,
            UpdatedAt: updatedAt);
    }
    catch (Exception ex)
    {
        _logger.LogWarning(ex, "Failed to fetch canonical global market stats from CoinGecko");
        return null;
    }
}

private async Task<FearGreedSnapshot?> TryFetchFearGreedAsync(CancellationToken ct)
{
    var url = $"{_settings.FearGreedBaseUrl.TrimEnd('/')}/fng/?limit=1&format=json";
    try
    {
        using var http = _httpClientFactory.CreateClient(nameof(MarketServiceClient));
        using var response = await http.GetAsync(url, ct);
        response.EnsureSuccessStatusCode();

        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

        if (!doc.RootElement.TryGetProperty("data", out var data)
            || data.ValueKind != JsonValueKind.Array
            || data.GetArrayLength() == 0)
        {
            throw new InvalidOperationException("Fear & Greed endpoint returned no data rows");
        }

        var current = data[0];
        var value = TryGetInt32(current, "value");
        var label = GetString(current, "value_classification")?.Trim();
        var updatedAt = TryGetUnixSeconds(current, "timestamp") ?? DateTimeOffset.UtcNow;

        return new FearGreedSnapshot(
            Value: value > 0 ? value : null,
            Label: string.IsNullOrWhiteSpace(label) ? null : label,
            UpdatedAt: updatedAt);
    }
    catch (Exception ex)
    {
        _logger.LogWarning(ex, "Failed to fetch canonical fear and greed index");
        return null;
    }
}

private static DateTimeOffset? ResolveCompositeUpdatedAt(params DateTimeOffset?[] values)
{
    var available = values.Where(value => value.HasValue).Select(value => value!.Value).ToArray();
    if (available.Length == 0)
    {
        return null;
    }

    return available.Min();
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

    public async Task<ServiceResult<MarketRealtimeQuotesResponse>> GetRealtimeQuotesAsync(
        IReadOnlyList<string> symbols,
        string? exchange = null,
        CancellationToken ct = default)
    {
        var requestedSymbols = (symbols ?? Array.Empty<string>())
            .Where(static item => !string.IsNullOrWhiteSpace(item))
            .Select(static item => item.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requestedSymbols.Length == 0)
        {
            return ServiceResult<MarketRealtimeQuotesResponse>.Fail("At least one symbol is required");
        }

        var normalizedExchange = NormalizeExchange(exchange);
        var snapshot = await LoadSnapshotAsync(ct);
        var snapshotLookup = snapshot.Items.ToDictionary(item => item.Symbol, StringComparer.OrdinalIgnoreCase);
        var liveRows = await LoadRealtimeRowsAsync(normalizedExchange, ct);

        var liveLookup = liveRows
            .Where(item => requestedSymbols.Contains(item.Symbol, StringComparer.OrdinalIgnoreCase))
            .GroupBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderBy(item => item.LagMs)
                    .ThenBy(item => ExchangePriority(item.Exchange))
                    .ThenByDescending(item => item.UpdatedAt)
                    .First(),
                StringComparer.OrdinalIgnoreCase);

        var items = new List<MarketRealtimeQuoteDto>(requestedSymbols.Length);
        var missing = new List<string>();
        var degradedFields = new HashSet<string>(snapshot.DegradedFields, StringComparer.OrdinalIgnoreCase);

        foreach (var symbol in requestedSymbols)
        {
            snapshotLookup.TryGetValue(symbol, out var snapshotItem);

            if (liveLookup.TryGetValue(symbol, out var liveItem))
            {
                items.Add(ToRealtimeQuoteDto(symbol, snapshotItem, liveItem));
                continue;
            }

            if (snapshotItem is not null)
            {
                degradedFields.Add("realtimePrice");
                items.Add(ToSnapshotFallbackQuoteDto(snapshotItem));
                continue;
            }

            degradedFields.Add("realtimePrice");
            missing.Add(symbol);
        }

        return ServiceResult<MarketRealtimeQuotesResponse>.Ok(new MarketRealtimeQuotesResponse
        {
            Items = items,
            MissingSymbols = missing,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = items.Count > 0 ? items.Max(static item => item.UpdatedAt) : snapshot.UpdatedAt,
                DegradedFields = degradedFields.ToArray(),
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
                var config = await _marketConfig.GetConfigAsync(null, ct);
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


    private static int TryGetInt32(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return 0;
        }

        return property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetInt32(out var numberValue) => numberValue,
            JsonValueKind.Number when property.TryGetInt64(out var longValue) => (int)longValue,
            JsonValueKind.String when int.TryParse(property.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0,
        };
    }

    private static decimal? TryGetNestedDecimal(JsonElement item, string container, string propertyName)
    {
        if (!item.TryGetProperty(container, out var containerEl) || containerEl.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var value = GetDecimal(containerEl, propertyName);
        return value > 0 ? value : null;
    }

    private static DateTimeOffset? TryGetUnixSeconds(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        long seconds = property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetInt64(out var numberValue) => numberValue,
            JsonValueKind.String when long.TryParse(property.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0,
        };

        return seconds > 0 ? DateTimeOffset.FromUnixTimeSeconds(seconds) : null;
    }

    private async Task<IReadOnlyList<RealtimeWatcherRow>> LoadRealtimeRowsAsync(string? exchange, CancellationToken ct)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);

        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataMarketWatcherRows,
                new { exchange, limit = RealtimeRowsLimit, offset = 0 },
                timeout,
                ct);

            if (reply.ValueKind != JsonValueKind.Object)
            {
                return [];
            }

            if (reply.TryGetProperty("error", out var errorEl))
            {
                _logger.LogWarning("Realtime watcher rows request returned error: {Error}", errorEl.GetString());
                return [];
            }

            if (!reply.TryGetProperty("items", out var itemsEl) || itemsEl.ValueKind != JsonValueKind.Array)
            {
                return [];
            }

            var rows = new List<RealtimeWatcherRow>();
            foreach (var item in itemsEl.EnumerateArray())
            {
                var parsed = ParseRealtimeWatcherRow(item);
                if (parsed is not null)
                {
                    rows.Add(parsed);
                }
            }

            return rows;
        }
        catch (TimeoutException ex)
        {
            _logger.LogWarning(ex, "Realtime watcher rows request timed out");
            return [];
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Realtime watcher rows request failed");
            return [];
        }
    }

    private static RealtimeWatcherRow? ParseRealtimeWatcherRow(JsonElement item)
    {
        var symbol = GetString(item, "symbol")?.Trim().ToUpperInvariant();
        if (string.IsNullOrWhiteSpace(symbol))
        {
            return null;
        }

        var price = GetDecimal(item, "last_price");
        if (price <= 0)
        {
            return null;
        }

        var updatedAt = GetDateTimeOffset(item, "last_price_ts");
        if (updatedAt is null)
        {
            return null;
        }

        return new RealtimeWatcherRow(
            Symbol: symbol,
            Exchange: NormalizeExchange(GetString(item, "exchange")),
            RealtimeSymbol: GetString(item, "realtime_symbol")?.Trim(),
            Price: DecimalRound(price),
            UpdatedAt: updatedAt.Value,
            LagMs: GetLong(item, "lag_ms"));
    }

    private static MarketRealtimeQuoteDto ToRealtimeQuoteDto(
        string symbol,
        SnapshotTicker? snapshotItem,
        RealtimeWatcherRow liveItem)
    {
        return new MarketRealtimeQuoteDto
        {
            Symbol = symbol,
            Price = liveItem.Price,
            Change24h = snapshotItem?.Change24h ?? 0,
            High24h = snapshotItem?.High24h ?? 0,
            Low24h = snapshotItem?.Low24h ?? 0,
            Volume24h = snapshotItem?.Volume24h ?? 0,
            Exchange = liveItem.Exchange,
            RealtimeSymbol = liveItem.RealtimeSymbol,
            LagMs = liveItem.LagMs,
            Source = RealtimeSource,
            IsFallback = false,
            UpdatedAt = liveItem.UpdatedAt,
        };
    }

    private static MarketRealtimeQuoteDto ToSnapshotFallbackQuoteDto(SnapshotTicker snapshotItem)
    {
        return new MarketRealtimeQuoteDto
        {
            Symbol = snapshotItem.Symbol,
            Price = snapshotItem.Price,
            Change24h = snapshotItem.Change24h,
            High24h = snapshotItem.High24h,
            Low24h = snapshotItem.Low24h,
            Volume24h = snapshotItem.Volume24h,
            Exchange = null,
            RealtimeSymbol = null,
            LagMs = null,
            Source = SnapshotFallbackSource,
            IsFallback = true,
            UpdatedAt = snapshotItem.UpdatedAt,
        };
    }

    private static DateTimeOffset? GetDateTimeOffset(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        return property.ValueKind switch
        {
            JsonValueKind.String when DateTimeOffset.TryParse(
                property.GetString(),
                CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                out var dateValue) => dateValue,
            _ => null,
        };
    }

    private static long? GetLong(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        return property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetInt64(out var numberValue) => numberValue,
            JsonValueKind.String when long.TryParse(property.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => null,
        };
    }

    private static string? NormalizeExchange(string? exchange)
    {
        return string.IsNullOrWhiteSpace(exchange)
            ? null
            : exchange.Trim().ToLowerInvariant();
    }

    private static int ExchangePriority(string? exchange)
    {
        return exchange switch
        {
            "bybit" => 0,
            "binance" => 1,
            "kraken" => 2,
            _ => 10,
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

    private sealed record CanonicalOverviewEnvelope(
        decimal? TotalMarketCapUsd,
        decimal? BtcDominance,
        decimal? Volume24hUsd,
        int? ActiveAssets,
        int? FearGreedValue,
        string? FearGreedLabel,
        DateTimeOffset? UpdatedAt,
        IReadOnlyList<string> DegradedFields);

    private sealed record CoinGeckoGlobalSnapshot(
        decimal? TotalMarketCapUsd,
        decimal? BtcDominance,
        decimal? Volume24hUsd,
        int? ActiveAssets,
        DateTimeOffset UpdatedAt);

    private sealed record FearGreedSnapshot(
        int? Value,
        string? Label,
        DateTimeOffset UpdatedAt);

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

    private sealed record RealtimeWatcherRow(
        string Symbol,
        string? Exchange,
        string? RealtimeSymbol,
        decimal Price,
        DateTimeOffset UpdatedAt,
        long? LagMs);
}
