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
        var key = $"market:config:full:{normalized}:v3";

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
        var symbols = await GetOrFetchSymbolsAsync(NormalizeExchange(exchange), ct);
        return symbols.Contains(symbol, StringComparer.OrdinalIgnoreCase);
    }

    // ── Private helpers ───────────────────────────────────────────────────

    private async Task<MarketConfigResponse> BuildConfigAsync(string exchange, CancellationToken ct)
    {
        var symbolsUpdatedAt = DateTimeOffset.UtcNow;
        var symbols = await GetOrFetchSymbolsAsync(exchange, ct);

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

    private async Task<IReadOnlyList<string>> GetOrFetchSymbolsAsync(string exchange, CancellationToken ct)
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
        var fresh = await FetchSymbolsForExchangeAsync(exchange, ct);
        if (fresh.Count > 0)
        {
            await _cache.SetAsync(key, new SymbolListWrapper(fresh.ToList()), ttl, ct);
        }
        return fresh;
    }

    private async Task<IReadOnlyList<string>> FetchSymbolsForExchangeAsync(string exchange, CancellationToken ct)
    {
        // 1) Try MW (single source of truth for what's actually persisted).
        var mwSymbols = await TryFetchTrackedFromMarketWatcherAsync(exchange, ct);
        if (mwSymbols.Count > 0)
        {
            return mwSymbols;
        }

        // 2) Fallback: Bybit instrument list (only meaningful for bybit) so the
        //    dropdown never goes empty during MW startup / cold-deploy.
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
