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
    // Serve-stale-on-error: the last successful global / Fear&Greed fetch.
    // CoinGecko's free tier intermittently 429s; without this, a failed refresh
    // cached null totalMarketCap/btcDominance for the cache window, which made
    // the frontend's guarded tiles disappear — the global-stats card visibly
    // collapsed from 6 tiles back to 4. We now reuse the last good values on a
    // transient failure (null only before the first success / right after a
    // gateway restart). Reference assignment is atomic; no locking needed.
    private static CoinGeckoGlobalSnapshot? _lastGoodGlobal;
    private static FearGreedSnapshot? _lastGoodFearGreed;

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

public async Task<ServiceResult<PublicMarketOverviewResponse>> GetPublicOverviewAsync(int trendingLimit = 5, CancellationToken ct = default)
{
    var snapshotTask = LoadSnapshotAsync(ct);
    var canonicalTask = LoadCanonicalOverviewAsync(ct);
    await Task.WhenAll(snapshotTask, canonicalTask);

    var snapshot = await snapshotTask;
    var canonical = await canonicalTask;

    var overviewDegradedFields = new HashSet<string>(canonical.DegradedFields, StringComparer.OrdinalIgnoreCase);
    var degradedFields = new HashSet<string>(overviewDegradedFields, StringComparer.OrdinalIgnoreCase);

    // The multi-window changes (change1h/7d/30d) are a tickers/list concern, not
    // part of the overview's trending projection (which only surfaces the trending
    // assets' price/24h move). Exclude them so they don't leak into the overview
    // payload as `trending.change1h` etc.
    var trendingDegradedFields = snapshot.DegradedFields
        .Where(static field =>
            !field.Equals("change1h", StringComparison.OrdinalIgnoreCase) &&
            !field.Equals("change7d", StringComparison.OrdinalIgnoreCase) &&
            !field.Equals("change30d", StringComparison.OrdinalIgnoreCase))
        .ToArray();
    if (trendingDegradedFields.Length > 0)
    {
        degradedFields.UnionWith(trendingDegradedFields.Select(field => $"trending.{field}"));
    }

    var degradedSections = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    if (overviewDegradedFields.Count > 0)
    {
        degradedSections.Add("marketOverview");
    }

    if (trendingDegradedFields.Length > 0)
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

        var snapshot = new CoinGeckoGlobalSnapshot(
            TotalMarketCapUsd: totalMarketCap > 0 ? DecimalRound(totalMarketCap.Value) : null,
            BtcDominance: btcDominance >= 0 ? DecimalRound(btcDominance.Value) : null,
            Volume24hUsd: totalVolume24h > 0 ? DecimalRound(totalVolume24h.Value) : null,
            ActiveAssets: activeAssets > 0 ? activeAssets : null,
            UpdatedAt: updatedAt);

        // Cache as last-good only when the core fields came back, so a later
        // transient failure can serve these instead of blanking the card.
        if (snapshot.TotalMarketCapUsd is not null && snapshot.BtcDominance is not null)
        {
            _lastGoodGlobal = snapshot;
        }

        return snapshot;
    }
    catch (Exception ex)
    {
        _logger.LogWarning(ex, "Failed to fetch canonical global market stats from CoinGecko; serving last-known-good if available");
        return _lastGoodGlobal;
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

        var snapshot = new FearGreedSnapshot(
            Value: value > 0 ? value : null,
            Label: string.IsNullOrWhiteSpace(label) ? null : label,
            UpdatedAt: updatedAt);

        if (snapshot.Value is not null)
        {
            _lastGoodFearGreed = snapshot;
        }

        return snapshot;
    }
    catch (Exception ex)
    {
        _logger.LogWarning(ex, "Failed to fetch canonical fear and greed index; serving last-known-good if available");
        return _lastGoodFearGreed;
    }
}

    /// <inheritdoc />
    public async Task<ServiceResult<MarketGlobalSummaryResponse>> GetGlobalSummaryAsync(
        string? exchange = null,
        CancellationToken ct = default)
    {
        var normalizedExchange = NormalizeSummaryExchange(exchange);
        var ttl = TimeSpan.FromSeconds(Math.Max(5, _settings.GlobalSummaryCacheTtlSeconds));
        var cacheKey = $"{GlobalSummaryCacheKeyPrefix}:{normalizedExchange}";

        var summary = await _cache.GetOrCreateAsync(
            cacheKey,
            ttl,
            () => BuildGlobalSummaryAsync(normalizedExchange, ct),
            ct);

        return ServiceResult<MarketGlobalSummaryResponse>.Ok(summary);
    }

    /// Aggregate the snapshot + canonical-overview feeds across the
    /// MW-tracked symbol universe for [exchange]. Honest about what's
    /// missing: when the snapshot has no 24h-change for a tracked symbol
    /// (e.g. a Binance-only symbol that Bybit doesn't carry), that symbol
    /// still counts toward TrackedCount but contributes zero to the
    /// gainers/losers/avg-change breakdown — and the meta lists it as
    /// a degraded field so the client can show a hint.
    private async Task<MarketGlobalSummaryResponse> BuildGlobalSummaryAsync(
        string exchange,
        CancellationToken ct)
    {
        var configTask = _marketConfig.GetConfigAsync(exchange, ct);
        var snapshotTask = LoadSnapshotAsync(ct);
        var canonicalTask = LoadCanonicalOverviewAsync(ct);
        await Task.WhenAll(configTask, snapshotTask, canonicalTask);

        var config = await configTask;
        var snapshot = await snapshotTask;
        var canonical = await canonicalTask;

        var tracked = (config.Symbols ?? Array.Empty<string>())
            .Where(static s => !string.IsNullOrWhiteSpace(s))
            .Select(static s => s.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var snapshotBySymbol = snapshot.Items.ToDictionary(
            item => item.Symbol,
            StringComparer.OrdinalIgnoreCase);

        // Per-symbol contributions. Symbols not present in the Bybit
        // snapshot are skipped from change-stats (we have no 24h data
        // for them) but still counted as tracked.
        var gainers = 0;
        var losers = 0;
        var contributingCount = 0;
        decimal volumeSum = 0m;
        decimal changeSum = 0m;            // equal-weight fallback
        decimal weightedChangeSum = 0m;    // Σ (change · volume)
        var missingChangeData = new List<string>();

        foreach (var symbol in tracked)
        {
            if (!snapshotBySymbol.TryGetValue(symbol, out var ticker))
            {
                missingChangeData.Add(symbol);
                continue;
            }

            volumeSum += ticker.Volume24h;
            weightedChangeSum += ticker.Change24h * ticker.Volume24h;

            // Treat zero-change as neither gainer nor loser (matches the
            // existing UI semantics).
            if (ticker.Change24h > 0)
            {
                gainers++;
            }
            else if (ticker.Change24h < 0)
            {
                losers++;
            }
            changeSum += ticker.Change24h;
            contributingCount++;
        }

        // Volume-weighted average 24h change — bigger-turnover pairs move the
        // number more (reflects how the market moved by money, not by coin
        // count). Falls back to a plain mean only if total volume is zero.
        var averageChange = volumeSum > 0
            ? decimal.Round(weightedChangeSum / volumeSum, 2, MidpointRounding.AwayFromZero)
            : (contributingCount > 0
                ? decimal.Round(changeSum / contributingCount, 2, MidpointRounding.AwayFromZero)
                : 0m);

        // Sentiment: prefer canonical Fear&Greed; otherwise synthesise
        // from breadth (gainers / contributing) and clamped average
        // 24h move. Keeps the client logic trivial.
        int sentimentValue;
        string sentimentLabel;
        string sentimentSource;
        if (canonical.FearGreedValue is int fg && fg > 0)
        {
            sentimentValue = Math.Clamp(fg, 0, 100);
            sentimentLabel = string.IsNullOrWhiteSpace(canonical.FearGreedLabel)
                ? SentimentLabelFor(sentimentValue)
                : canonical.FearGreedLabel;
            sentimentSource = "fear_greed";
        }
        else if (contributingCount > 0)
        {
            var breadth = (double)gainers / contributingCount;
            var clampedMove = Math.Clamp((double)averageChange, -8.0, 8.0);
            var moveScore = (clampedMove + 8.0) / 16.0;
            sentimentValue = (int)Math.Round(((breadth * 0.75) + (moveScore * 0.25)) * 100);
            sentimentValue = Math.Clamp(sentimentValue, 0, 100);
            sentimentLabel = SentimentLabelFor(sentimentValue);
            sentimentSource = "synthetic";
        }
        else
        {
            sentimentValue = 50;
            sentimentLabel = SentimentLabelFor(50);
            sentimentSource = "synthetic";
        }

        var degradedFields = new List<string>();
        if (missingChangeData.Count > 0)
        {
            degradedFields.Add("change24h");
            degradedFields.Add("volume24h");
        }

        return new MarketGlobalSummaryResponse
        {
            Exchange = exchange,
            TrackedCount = tracked.Length,
            Gainers = gainers,
            Losers = losers,
            TotalVolume24h = DecimalRound(volumeSum),
            AverageChange24h = averageChange,
            SentimentValue = sentimentValue,
            SentimentLabel = sentimentLabel,
            SentimentSource = sentimentSource,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                // Freshness must reflect the LIVE price snapshot, not the daily
                // Fear&Greed timestamp. The old composite took the OLDEST of the
                // two, so the once-a-day F&G stamp dragged the card to look ~22h
                // stale every day even though prices were seconds old.
                UpdatedAt = snapshot.UpdatedAt,
                DegradedFields = degradedFields,
                DegradedSections = missingChangeData.Count > 0
                    ? new[] { "trackedChange" }
                    : Array.Empty<string>(),
            },
        };
    }

    private static string SentimentLabelFor(int value)
    {
        if (value <= 20) return "Extreme Fear";
        if (value <= 40) return "Fear";
        if (value < 60) return "Neutral";
        if (value < 80) return "Greed";
        return "Extreme Greed";
    }

    private static string NormalizeSummaryExchange(string? exchange)
    {
        if (string.IsNullOrWhiteSpace(exchange))
        {
            return "bybit";
        }
        var trimmed = exchange.Trim().ToLowerInvariant();
        return trimmed switch
        {
            "bybit" or "binance" => trimmed,
            _ => "bybit",
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
}
