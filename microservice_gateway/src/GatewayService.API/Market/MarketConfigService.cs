using System.Text.Json;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed class MarketConfigService : IMarketConfigService
{
    private const string DefaultExchange = "bybit";

    private readonly IBybitSymbolProvider   _bybit;
    private readonly IMarketCacheService    _cache;
    private readonly IKafkaRequestClient    _kafka;
    private readonly MarketSettings         _settings;
    private readonly ILogger<MarketConfigService> _log;

    public MarketConfigService(
        IBybitSymbolProvider bybit,
        IMarketCacheService cache,
        IKafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<MarketConfigService> log)
    {
        _bybit    = bybit;
        _cache    = cache;
        _kafka    = kafka;
        _settings = settings.Value;
        _log      = log;
    }

    /// <inheritdoc />
    public async Task<MarketConfigResponse> GetConfigAsync(string? exchange = null, CancellationToken ct = default)
    {
        var normalized = NormalizeExchange(exchange);
        var ttl = TimeSpan.FromSeconds(_settings.ConfigCacheTtlSeconds);
        var key = $"market:config:full:{normalized}:v4";

        var cached = await _cache.GetAsync<MarketConfigResponse>(key, ct);
        if (cached is { Symbols.Count: > 0 })
        {
            return cached;
        }

        var fresh = await BuildConfigAsync(normalized, ct);
        if (fresh.Symbols.Count > 0)
        {
            await _cache.SetAsync(key, fresh, ttl, ct);
        }
        return fresh;
    }

    /// <inheritdoc />
    public async Task<bool> IsKnownSymbolAsync(string symbol, string? exchange = null, CancellationToken ct = default)
    {
        var symbols = await GetOrFetchSymbolsAsync(NormalizeExchange(exchange), Array.Empty<string>(), ct);
        return symbols.Contains(symbol, StringComparer.OrdinalIgnoreCase);
    }

    // ── Private helpers ───────────────────────────────────────────────────

    private async Task<MarketConfigResponse> BuildConfigAsync(string exchange, CancellationToken ct)
    {
        var symbolsUpdatedAt = DateTimeOffset.UtcNow;
        var centerPairs = await TryFetchCenterPairsAsync(ct);
        var symbols = await GetOrFetchSymbolsAsync(exchange, centerPairs.Symbols, ct);

        var timeframeDtos = TimeframeMap.All
            .Select(tf => new TimeframeDto(
                Id:     tf.Id,
                Label:  tf.Label,
                Class:  tf.Class.ToString().ToLowerInvariant(),
                StepMs: (int)tf.StepMs))
            .ToList();

        var heavyTfIds  = TimeframeMap.All.Where(tf => tf.Class == TimeframeClass.Heavy )
                                          .Select(tf => tf.Id).ToList();
        var mediumTfIds = TimeframeMap.All.Where(tf => tf.Class == TimeframeClass.Medium)
                                          .Select(tf => tf.Id).ToList();
        var lightTfIds  = TimeframeMap.All.Where(tf => tf.Class == TimeframeClass.Light )
                                          .Select(tf => tf.Id).ToList();

        var constraints = new CandleCountConstraintsDto
        {
            Heavy           = CandleCountGrid.Heavy,
            Medium          = CandleCountGrid.Medium,
            Light           = CandleCountGrid.Light,
            HeavyTimeframes  = heavyTfIds,
            MediumTimeframes = mediumTfIds,
            LightTimeframes  = lightTfIds,
        };

        var defaults = new MarketDefaultsDto(
            Symbol:      _settings.DefaultSymbol,
            Timeframe:   _settings.DefaultTimeframe,
            CandleCount: _settings.DefaultCandleCount);

        var response = new MarketConfigResponse
        {
            Symbols          = symbols,
            Quotes           = centerPairs.Quotes,
            Timeframes       = timeframeDtos,
            CandleCounts     = constraints,
            Defaults         = defaults,
            CachedAt         = DateTimeOffset.UtcNow,
            SymbolsUpdatedAt = symbolsUpdatedAt,
        };

        _log.LogInformation(
            "Built market config for {Exchange}: {SymbolCount} symbols, {TfCount} timeframes",
            exchange, symbols.Count, timeframeDtos.Count);

        return response;
    }

    private async Task<IReadOnlyList<string>> GetOrFetchSymbolsAsync(string exchange, IReadOnlyList<string> centerFallback, CancellationToken ct)
    {
        var ttl = TimeSpan.FromSeconds(_settings.SymbolsCacheTtlSeconds);
        var key = $"market:config:symbols:{exchange}:v2";

        // Fast path: hit cache.
        var cached = await _cache.GetAsync<SymbolListWrapper>(key, ct);
        if (cached is { Symbols.Count: > 0 })
        {
            return cached.Symbols;
        }

        // Cold or empty cache: rebuild. Do NOT cache empty results — MW may be
        // mid-startup and would otherwise stay empty for the full TTL window.
        var fresh = await FetchSymbolsForExchangeAsync(exchange, centerFallback, ct);
        if (fresh.Count > 0)
        {
            await _cache.SetAsync(key, new SymbolListWrapper(fresh.ToList()), ttl, ct);
        }
        return fresh;
    }

    private async Task<IReadOnlyList<string>> FetchSymbolsForExchangeAsync(string exchange, IReadOnlyList<string> centerFallback, CancellationToken ct)
    {
        // 1) Prefer the currency-pairs center — the single source of truth for
        //    the pairs MW is *configured* to track (e.g. 92). MW's live-row set
        //    is only the subset that has already produced a tick (e.g. 85), so
        //    using it under-reported the tracked-pairs count and dropped freshly
        //    added pairs from the list until their first candle landed.
        if (centerFallback.Count > 0)
        {
            return centerFallback
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(s => s, StringComparer.OrdinalIgnoreCase)
                .ToArray();
        }

        // 2) Center unavailable (mid-startup / outage) → fall back to MW's
        //    live-tracked rows so the dropdown is never empty.
        var mwSymbols = await TryFetchTrackedFromMarketWatcherAsync(exchange, ct);
        if (mwSymbols.Count > 0)
        {
            _log.LogInformation(
                "Currency-pairs center empty for {Exchange}; using MW live-tracked fallback ({Count} symbols)",
                exchange, mwSymbols.Count);
            return mwSymbols;
        }

        // 3) Last resort (total center+MW outage): Bybit live instrument list
        //    (only meaningful for bybit) so the dropdown never goes empty.
        if (string.Equals(exchange, "bybit", StringComparison.OrdinalIgnoreCase))
        {
            var bybitList = await _bybit.GetActiveSymbolsAsync(ct);
            _log.LogInformation(
                "MW returned no tracked symbols for {Exchange}; falling back to Bybit instrument list ({Count} symbols)",
                exchange, bybitList.Count);
            return bybitList;
        }

        _log.LogWarning(
            "MW returned no tracked symbols for {Exchange} and no fallback is available; returning empty list",
            exchange);
        return Array.Empty<string>();
    }

    private async Task<IReadOnlyList<string>> TryFetchTrackedFromMarketWatcherAsync(string exchange, CancellationToken ct)
    {
        var timeout = TimeSpan.FromSeconds(Math.Max(2, _settings.KafkaTimeoutSeconds));
        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataMarketWatcherTracked,
                new { exchange },
                timeout,
                ct);

            if (reply.ValueKind != JsonValueKind.Object)
            {
                return Array.Empty<string>();
            }

            if (reply.TryGetProperty("error", out var errorEl))
            {
                _log.LogWarning(
                    "MW tracked-symbols request for {Exchange} returned error: {Error}",
                    exchange, errorEl.GetString());
                return Array.Empty<string>();
            }

            if (!reply.TryGetProperty("symbols", out var symbolsEl) || symbolsEl.ValueKind != JsonValueKind.Array)
            {
                return Array.Empty<string>();
            }

            var result = new List<string>(symbolsEl.GetArrayLength());
            foreach (var item in symbolsEl.EnumerateArray())
            {
                var value = item.GetString();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    result.Add(value.Trim().ToUpperInvariant());
                }
            }
            return result
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(s => s, StringComparer.OrdinalIgnoreCase)
                .ToArray();
        }
        catch (TimeoutException ex)
        {
            _log.LogWarning(ex, "MW tracked-symbols request for {Exchange} timed out", exchange);
            return Array.Empty<string>();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "MW tracked-symbols request for {Exchange} failed", exchange);
            return Array.Empty<string>();
        }
    }

    /// <summary>
    /// Fetch the currency-pairs center state (cmd.data.pairs.list): the
    /// cross-product symbols and the active quote/stablecoin vocabulary. Used to
    /// populate config Quotes and as the symbol fallback. Soft-fails to empty.
    /// </summary>
    private async Task<(IReadOnlyList<string> Symbols, IReadOnlyList<string> Quotes)> TryFetchCenterPairsAsync(CancellationToken ct)
    {
        var timeout = TimeSpan.FromSeconds(Math.Max(2, _settings.KafkaTimeoutSeconds));
        try
        {
            var reply = await _kafka.RequestAsync(DataTopics.CmdDataPairsList, new { }, timeout, ct);
            if (reply.ValueKind != JsonValueKind.Object)
            {
                return (Array.Empty<string>(), Array.Empty<string>());
            }
            return (ReadStringArray(reply, "symbols"), ReadActiveAssets(reply, "quotes"));
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Currency-pairs center request (cmd.data.pairs.list) failed");
            return (Array.Empty<string>(), Array.Empty<string>());
        }
    }

    private static IReadOnlyList<string> ReadStringArray(JsonElement obj, string prop)
    {
        if (!obj.TryGetProperty(prop, out var el) || el.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        var list = new List<string>(el.GetArrayLength());
        foreach (var item in el.EnumerateArray())
        {
            var v = item.GetString();
            if (!string.IsNullOrWhiteSpace(v)) list.Add(v.Trim().ToUpperInvariant());
        }
        return list;
    }

    private static IReadOnlyList<string> ReadActiveAssets(JsonElement obj, string prop)
    {
        if (!obj.TryGetProperty(prop, out var el) || el.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        var list = new List<string>(el.GetArrayLength());
        foreach (var item in el.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object) continue;
            var active = !item.TryGetProperty("active", out var a) || a.ValueKind != JsonValueKind.False;
            if (!active) continue;
            var asset = item.TryGetProperty("asset", out var ae) ? ae.GetString() : null;
            if (!string.IsNullOrWhiteSpace(asset)) list.Add(asset.Trim().ToUpperInvariant());
        }
        return list;
    }

    private static string NormalizeExchange(string? exchange)
    {
        if (string.IsNullOrWhiteSpace(exchange))
        {
            return DefaultExchange;
        }

        var trimmed = exchange.Trim().ToLowerInvariant();
        return trimmed switch
        {
            "bybit" or "binance" => trimmed,
            _ => DefaultExchange,
        };
    }

    // Thin wrapper to give the symbol list a class type that IMarketCacheService<T> can serialise.
    private sealed record SymbolListWrapper(List<string> Symbols);
}
