using GatewayService.API.DTOs.Responses;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <inheritdoc />
public sealed class MarketConfigService : IMarketConfigService
{
    private const string SymbolsCacheKey = "market:config:symbols:v1";
    private const string ConfigCacheKey  = "market:config:full:v1";

    private readonly IBybitSymbolProvider   _bybit;
    private readonly IMarketCacheService    _cache;
    private readonly MarketSettings         _settings;
    private readonly ILogger<MarketConfigService> _log;

    public MarketConfigService(
        IBybitSymbolProvider bybit,
        IMarketCacheService cache,
        IOptions<MarketSettings> settings,
        ILogger<MarketConfigService> log)
    {
        _bybit    = bybit;
        _cache    = cache;
        _settings = settings.Value;
        _log      = log;
    }

    /// <inheritdoc />
    public async Task<MarketConfigResponse> GetConfigAsync(CancellationToken ct = default)
    {
        var ttl = TimeSpan.FromSeconds(_settings.ConfigCacheTtlSeconds);
        return await _cache.GetOrCreateAsync(ConfigCacheKey, ttl,
            () => BuildConfigAsync(ct), ct);
    }

    /// <inheritdoc />
    public async Task<bool> IsKnownSymbolAsync(string symbol, CancellationToken ct = default)
    {
        var symbols = await GetOrFetchSymbolsAsync(ct);
        return symbols.Contains(symbol, StringComparer.OrdinalIgnoreCase);
    }

    // ── Private helpers ───────────────────────────────────────────────────

    private async Task<MarketConfigResponse> BuildConfigAsync(CancellationToken ct)
    {
        var symbolsUpdatedAt = DateTimeOffset.UtcNow;
        var symbols = await GetOrFetchSymbolsAsync(ct);

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
            "Built market config: {SymbolCount} symbols, {TfCount} timeframes",
            symbols.Count, timeframeDtos.Count);

        return response;
    }

    private async Task<IReadOnlyList<string>> GetOrFetchSymbolsAsync(CancellationToken ct)
    {
        var ttl = TimeSpan.FromSeconds(_settings.SymbolsCacheTtlSeconds);
        return await _cache.GetOrCreateAsync<SymbolListWrapper>(
            SymbolsCacheKey, ttl,
            async () =>
            {
                var list = await _bybit.GetActiveSymbolsAsync(ct);
                return new SymbolListWrapper(list.ToList());
            },
            ct)
            .ContinueWith(t => (IReadOnlyList<string>)t.Result.Symbols, ct,
                TaskContinuationOptions.OnlyOnRanToCompletion,
                TaskScheduler.Default);
    }

    // Thin wrapper to give the symbol list a class type that IMarketCacheService<T> can serialise.
    private sealed record SymbolListWrapper(List<string> Symbols);
}
