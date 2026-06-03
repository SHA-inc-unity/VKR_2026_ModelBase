using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <summary>
/// Lazy, cache-backed implementation of <see cref="IMarketWindowChangeService"/>.
///
/// <para>
/// For each tracked symbol it fans out two <c>cmd.data.dataset.latest_rows</c>
/// queries against the gateway's own candle store (microservice_data):
/// ~31 daily closes (the <c>1d</c>/"D" table) to anchor the 7 d / 30 d windows and
/// ~2 hourly closes (the <c>60m</c>/"60" table) to anchor the 1 h window. The
/// change for each window is computed against the snapshot's <b>live</b> price:
/// <c>change = (livePrice − closeNAgo) / closeNAgo × 100</c>.
/// </para>
///
/// <para>
/// Mirrors <see cref="CoinMetadataService"/> exactly: lazy
/// <see cref="IMarketCacheService.GetOrCreateAsync"/> behind a JSON-serializable
/// envelope, no hosted service, and a soft-fail to an empty map on any error (a
/// window we cannot anchor stays <c>null</c> — "show what we have"). A cache-warm
/// read performs zero Kafka calls.
/// </para>
/// </summary>
public sealed class MarketWindowChangeService : IMarketWindowChangeService
{
    private const string CacheKey = "market:window-change:v1";

    // Data-service tables are keyed by the Bybit kline interval -> client id.
    // "D" -> the {symbol}_1d daily table; "60" -> the {symbol}_60m hourly table.
    private const string DailyInterval = "D";
    private const string HourlyInterval = "60";

    private const long DayMs = 86_400_000L;
    private const long HourMs = 3_600_000L;

    // ~31 daily closes is the nominal coverage for the 30 d window; we request a
    // few extra (34) so a 1-3 day gap in the daily series still leaves a candle
    // at/older than the 30 d edge to anchor against. 2 hourly closes give us the
    // "~1 h ago" anchor (newest is ~now, the previous one is ~1 h back).
    private const int DailyLimit = 34;
    private const int HourlyLimit = 2;

    // Must request the FULL OHLCV projection: the shared row parser (ParseRows)
    // reads `timestamp_ms` and DROPS any row where open_price/close_price == 0, so
    // a close-only projection yields zero usable rows (every row gets skipped).
    // Reuse the proven chart projection; 34 daily rows × 7 cols is still tiny.
    private static readonly IReadOnlyList<string> CloseColumns =
        DataServiceClient.ChartProjectionColumns;

    // Cap fan-out concurrency so a full-universe (~92 symbols) snapshot rebuild
    // doesn't flood the shared Kafka request client. Each symbol issues 2 queries.
    private const int MaxConcurrency = 8;

    private readonly IDataServiceClient _dataClient;
    private readonly IMarketCacheService _cache;
    private readonly MarketSettings _settings;
    private readonly ILogger<MarketWindowChangeService> _logger;

    public MarketWindowChangeService(
        IDataServiceClient dataClient,
        IMarketCacheService cache,
        IOptions<MarketSettings> settings,
        ILogger<MarketWindowChangeService> logger)
    {
        _dataClient = dataClient;
        _cache = cache;
        _settings = settings.Value;
        _logger = logger;
    }

    /// <inheritdoc />
    public async Task<IReadOnlyDictionary<string, WindowChange>> GetWindowChangesAsync(
        IReadOnlyDictionary<string, decimal> livePriceBySymbol,
        CancellationToken ct)
    {
        if (livePriceBySymbol.Count == 0)
        {
            return EmptyMap();
        }

        var ttl = TimeSpan.FromSeconds(Math.Max(1, _settings.WindowChangeCacheTtlSeconds));
        var envelope = await _cache.GetOrCreateAsync(
            CacheKey,
            ttl,
            () => FetchWindowChangesAsync(livePriceBySymbol, ct),
            ct);

        return envelope.Items;
    }

    private async Task<WindowChangeEnvelope> FetchWindowChangesAsync(
        IReadOnlyDictionary<string, decimal> livePriceBySymbol,
        CancellationToken ct)
    {
        try
        {
            var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var map = new Dictionary<string, WindowChange>(StringComparer.OrdinalIgnoreCase);

            using var gate = new SemaphoreSlim(MaxConcurrency, MaxConcurrency);

            var tasks = livePriceBySymbol
                .Where(static pair => !string.IsNullOrWhiteSpace(pair.Key) && pair.Value > 0m)
                .Select(async pair =>
                {
                    await gate.WaitAsync(ct);
                    try
                    {
                        var change = await ComputeForSymbolAsync(pair.Key, pair.Value, nowMs, ct);
                        return (Symbol: pair.Key, Change: change);
                    }
                    finally
                    {
                        gate.Release();
                    }
                })
                .ToArray();

            var results = await Task.WhenAll(tasks);
            foreach (var (symbol, change) in results)
            {
                // Only carry symbols that resolved at least one window — a fully
                // empty result is indistinguishable from "absent" to callers.
                if (change.Change1h.HasValue || change.Change7d.HasValue || change.Change30d.HasValue)
                {
                    map[symbol] = change;
                }
            }

            _logger.LogInformation(
                "Window changes refreshed: {Resolved}/{Requested} symbols carried at least one window",
                map.Count, livePriceBySymbol.Count);

            return new WindowChangeEnvelope(map);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to compute multi-window price changes; serving empty window map");
            return new WindowChangeEnvelope(new Dictionary<string, WindowChange>(StringComparer.OrdinalIgnoreCase));
        }
    }

    /// <summary>
    /// Computes the 1 h / 7 d / 30 d windows for a single symbol. Soft-fails to
    /// <see cref="WindowChange.Empty"/> on any per-symbol error so one bad symbol
    /// never poisons the whole map.
    /// </summary>
    private async Task<WindowChange> ComputeForSymbolAsync(
        string symbol,
        decimal livePrice,
        long nowMs,
        CancellationToken ct)
    {
        try
        {
            var dailyTask = _dataClient.GetLatestWindowRowsAsync(
                symbol, DailyInterval, DayMs, DailyLimit, CloseColumns, ct: ct);
            var hourlyTask = _dataClient.GetLatestWindowRowsAsync(
                symbol, HourlyInterval, HourMs, HourlyLimit, CloseColumns, ct: ct);

            await Task.WhenAll(dailyTask, hourlyTask);

            var daily = await dailyTask;
            var hourly = await hourlyTask;

            // 7 d / 30 d anchor to the newest daily candle at/before the window edge;
            // 1 h anchors to the newest hourly candle at/before now − 1 h. In every
            // case "no candle old enough / non-positive close" → that window stays null.
            var change7d = ChangeFromNewestBeforeCutoff(livePrice, daily, nowMs - 7 * DayMs);
            var change30d = ChangeFromNewestBeforeCutoff(livePrice, daily, nowMs - 30 * DayMs);
            var change1h = ChangeFromNewestBeforeCutoff(livePrice, hourly, nowMs - HourMs);

            return new WindowChange(change1h, change7d, change30d);
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Window change computation failed for {Symbol}; leaving its windows null", symbol);
            return WindowChange.Empty;
        }
    }

    /// <summary>
    /// Picks the candle whose timestamp is the NEWEST one that is still at or before
    /// <paramref name="cutoffMs"/> (nearest-but-not-after the window edge), then
    /// returns the % change of <paramref name="livePrice"/> against its close.
    /// Missing / empty / failed rows, no candle old enough, or a non-positive
    /// anchor close → <c>null</c> (that window stays null — "show what we have").
    /// </summary>
    private static decimal? ChangeFromNewestBeforeCutoff(decimal livePrice, RowsFetchResult rows, long cutoffMs)
    {
        // Empty / claim-check / failure all surface as HasRows == false here.
        if (!rows.HasRows)
        {
            return null;
        }

        decimal? anchorClose = null;
        long anchorTs = long.MinValue;
        foreach (var row in rows.Rows)
        {
            if (row.TimestampMs <= cutoffMs && row.TimestampMs > anchorTs && row.Close > 0m)
            {
                anchorTs = row.TimestampMs;
                anchorClose = row.Close;
            }
        }

        return ToChangePercent(livePrice, anchorClose);
    }

    private static decimal? ToChangePercent(decimal livePrice, decimal? closeNAgo)
    {
        if (closeNAgo is not { } anchor || anchor <= 0m)
        {
            return null;
        }

        var pct = (livePrice - anchor) / anchor * 100m;
        return decimal.Round(pct, 6, MidpointRounding.AwayFromZero);
    }

    private static IReadOnlyDictionary<string, WindowChange> EmptyMap() =>
        new Dictionary<string, WindowChange>(StringComparer.OrdinalIgnoreCase);

    /// <summary>
    /// JSON-serializable cache envelope (the distributed cache round-trips via JSON;
    /// wrapping the dictionary in a class keeps deserialization unambiguous —
    /// mirrors <see cref="CoinMetadataService.CoinMetadataEnvelope"/>).
    /// </summary>
    public sealed record WindowChangeEnvelope(Dictionary<string, WindowChange> Items);
}
