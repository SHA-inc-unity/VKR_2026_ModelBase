using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Clients.Market;

public sealed partial class MarketServiceClient
{
    private async Task<SnapshotEnvelope> LoadSnapshotAsync(CancellationToken ct)
    {
        return await _cache.GetOrCreateAsync(
            SnapshotCacheKey,
            TimeSpan.FromSeconds(Math.Max(1, _settings.SnapshotCacheTtlSeconds)),
            async () =>
            {
                var configTask = _marketConfig.GetConfigAsync(null, ct);
                var metadataTask = _coinMetadata.GetMetadataAsync(ct);
                await Task.WhenAll(configTask, metadataTask);
                var config = await configTask;
                var metadata = await metadataTask;
                return await FetchSnapshotAsync(config.Symbols, metadata, ct);
            },
            ct);
    }

    private async Task<SnapshotEnvelope> FetchSnapshotAsync(
        IReadOnlyList<string> activeSymbols,
        IReadOnlyDictionary<string, CoinMetadata> metadata,
        CancellationToken ct)
    {
        var activeSet = activeSymbols
            .Where(static item => !string.IsNullOrWhiteSpace(item))
            .Select(static item => item.Trim().ToUpperInvariant())
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        var updatedAt = DateTimeOffset.UtcNow;

        // Linear (perpetuals) is the primary source. Spot fills the symbols Bybit
        // lists ONLY on spot (e.g. PEPE, SHIB, FET, NEXO) — without it those
        // tracked pairs fell back to a zero ticker, which both showed $0 on the
        // market board and left them out of the gainers/losers breakdown
        // (the "92 tracked vs 85 measured" inconsistency).
        var linearTask = FetchBybitCategoryAsync("linear", activeSet, updatedAt, ct);
        var spotTask = FetchBybitCategoryAsync("spot", activeSet, updatedAt, ct);
        await Task.WhenAll(linearTask, spotTask);
        var linear = await linearTask;
        var spot = await spotTask;

        var items = activeSymbols
            .Select(symbol =>
                linear.TryGetValue(symbol, out var l) ? l
                : spot.TryGetValue(symbol, out var s) ? s
                : BuildFallbackTicker(symbol, updatedAt))
            .Select(ticker => EnrichWithMetadata(ticker, metadata))
            .ToArray();

        // If neither category yielded anything the whole snapshot is fallback.
        var allFallback = linear.Count == 0 && spot.Count == 0;
        if (allFallback)
        {
            _logger.LogWarning("Market snapshot fetch failed for both linear and spot; serving empty snapshot");
        }
        var degradedFields = BuildDegradedFields(items, allFallback);
        return FinalizeSnapshot(items, updatedAt, degradedFields);
    }

    /// <summary>
    /// Fetches one Bybit ticker category (linear/spot) and returns the tracked
    /// symbols it carries. Soft-fails to an empty map so a single category
    /// outage can't take down the whole snapshot.
    /// </summary>
    private async Task<Dictionary<string, SnapshotTicker>> FetchBybitCategoryAsync(
        string category, HashSet<string> activeSet, DateTimeOffset updatedAt, CancellationToken ct)
    {
        var tickers = new Dictionary<string, SnapshotTicker>(StringComparer.OrdinalIgnoreCase);
        var url = $"{_settings.BybitBaseUrl}/v5/market/tickers?category={category}";
        try
        {
            using var http = _httpClientFactory.CreateClient(nameof(MarketServiceClient));
            using var response = await http.GetAsync(url, ct);
            response.EnsureSuccessStatusCode();

            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
            if (!doc.RootElement.TryGetProperty("retCode", out var retCodeEl) || retCodeEl.GetInt32() != 0)
            {
                return tickers;
            }
            if (!doc.RootElement.TryGetProperty("result", out var resultEl)
                || !resultEl.TryGetProperty("list", out var listEl))
            {
                return tickers;
            }

            foreach (var item in listEl.EnumerateArray())
            {
                var symbol = GetString(item, "symbol")?.Trim().ToUpperInvariant();
                if (string.IsNullOrWhiteSpace(symbol) || !activeSet.Contains(symbol))
                {
                    continue;
                }
                tickers[symbol] = BuildTicker(symbol, item, updatedAt);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Bybit {Category} tickers fetch failed", category);
        }
        return tickers;
    }

    private static SnapshotEnvelope FinalizeSnapshot(IReadOnlyList<SnapshotTicker> items, DateTimeOffset updatedAt, IReadOnlyList<string> degradedFields)
    {
        // Rank by the REAL circulating-supply market cap, with coins of unknown
        // supply (null cap) ordered last and broken by liquidity/symbol. We no
        // longer rank by the old open-interest proxy — it produced wrong caps.
        var ranked = items
            .OrderByDescending(item => item.MarketCap.HasValue)
            .ThenByDescending(item => item.MarketCap ?? 0m)
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
            DecimalRound(ranked.Sum(item => item.MarketCap ?? 0m)),
            DecimalRound(ranked.FirstOrDefault(item => string.Equals(item.Symbol, "BTCUSDT", StringComparison.OrdinalIgnoreCase))?.MarketCap ?? 0m),
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
        // openInterestValue/turnover kept only as an internal rank tie-breaker
        // hint; it is NOT surfaced as the displayed market cap any more.
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
            MarketCap: null,
            CirculatingSupply: null,
            TotalSupply: null,
            MaxSupply: null,
            Fdv: null,
            Ath: null,
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
            MarketCap: null,
            CirculatingSupply: null,
            TotalSupply: null,
            MaxSupply: null,
            Fdv: null,
            Ath: null,
            High24h: 0,
            Low24h: 0,
            Rank: 0,
            LogoUrl: BuildLogoUrl(baseAsset),
            ExchangeCount: 1,
            UpdatedAt: updatedAt,
            IsTrending: false,
            TrendingScore: 0);
    }

    /// <summary>
    /// Overlays CoinGecko supply/ATH metadata onto a freshly-built ticker and
    /// computes the real market cap + FDV from the LIVE Bybit price:
    /// <c>marketCap = circulatingSupply × price</c>,
    /// <c>fdv = (maxSupply ?? totalSupply) × price</c>.
    /// When supply is unknown (base unmapped / CoinGecko miss) or there is no live
    /// price, the cap/FDV fields stay null — we deliberately do NOT fall back to
    /// the old open-interest proxy for the displayed cap.
    /// </summary>
    private static SnapshotTicker EnrichWithMetadata(
        SnapshotTicker ticker,
        IReadOnlyDictionary<string, CoinMetadata> metadata)
    {
        if (!metadata.TryGetValue(ticker.BaseAsset, out var meta))
        {
            return ticker;
        }

        decimal? marketCap = null;
        if (meta.CirculatingSupply is > 0 && ticker.Price > 0)
        {
            marketCap = DecimalRound(meta.CirculatingSupply.Value * ticker.Price);
        }

        decimal? fdv = null;
        var fdvSupply = meta.MaxSupply ?? meta.TotalSupply;
        if (fdvSupply is > 0 && ticker.Price > 0)
        {
            fdv = DecimalRound(fdvSupply.Value * ticker.Price);
        }

        return ticker with
        {
            MarketCap = marketCap,
            CirculatingSupply = meta.CirculatingSupply.HasValue ? DecimalRound(meta.CirculatingSupply.Value) : null,
            TotalSupply = meta.TotalSupply.HasValue ? DecimalRound(meta.TotalSupply.Value) : null,
            MaxSupply = meta.MaxSupply.HasValue ? DecimalRound(meta.MaxSupply.Value) : null,
            Fdv = fdv,
            Ath = meta.Ath.HasValue ? DecimalRound(meta.Ath.Value) : null,
        };
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
        // Real market cap is degraded when any tracked coin has no known supply
        // (unmapped base / CoinGecko miss / missing live price) → null cap.
        if (items.Any(item => item.MarketCap is null)) degraded.Add("marketCap");
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

    private static decimal ComputeTrendingScore(decimal change24h, decimal volume24h)
    {
        var weightedMagnitude = Math.Abs(change24h);
        var liquidityBoost = (decimal)Math.Log10((double)Math.Max(volume24h, 1m));
        return DecimalRound(weightedMagnitude * Math.Max(liquidityBoost, 1m));
    }

    private sealed record SnapshotEnvelope(
        IReadOnlyList<SnapshotTicker> Items,
        DateTimeOffset UpdatedAt,
        decimal TotalVolume24hUsd,
        // Now summed from the REAL circulating-supply caps across the tracked
        // universe (coins with unknown supply contribute 0), not the OI proxy.
        decimal TotalMarketCapUsd,
        decimal BtcMarketCapUsd,
        IReadOnlyList<string> DegradedFields);

    private sealed record SnapshotTicker(
        string Symbol,
        string DisplayName,
        string BaseAsset,
        string QuoteAsset,
        decimal Price,
        decimal Change24h,
        decimal Volume24h,
        // Internal-only open-interest/turnover hint kept for diagnostics; never
        // surfaced as the displayed cap (the real cap lives in MarketCap below).
        decimal MarketCapProxy,
        decimal? MarketCap,
        decimal? CirculatingSupply,
        decimal? TotalSupply,
        decimal? MaxSupply,
        decimal? Fdv,
        decimal? Ath,
        decimal High24h,
        decimal Low24h,
        int Rank,
        string? LogoUrl,
        int ExchangeCount,
        DateTimeOffset UpdatedAt,
        bool IsTrending,
        decimal TrendingScore);
}
