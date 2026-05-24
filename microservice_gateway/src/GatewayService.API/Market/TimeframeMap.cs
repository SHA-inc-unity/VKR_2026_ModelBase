namespace GatewayService.API.Market;

/// <summary>
/// Classifies a timeframe by approximate data weight / query cost.
/// Determines which candle-count grid the timeframe belongs to.
/// </summary>
public enum TimeframeClass
{
    /// <summary>1m, 3m, 5m — high-frequency, largest tables.</summary>
    Heavy,

    /// <summary>15m, 30m, 60m, 120m, 240m — medium tables.</summary>
    Medium,

    /// <summary>360m, 720m, 1d — low-frequency, small tables.</summary>
    Light,
}

/// <summary>
/// Immutable descriptor for a single client-facing timeframe.
/// </summary>
public sealed record TimeframeInfo(
    /// <summary>External client id, e.g. "5m", "1d".</summary>
    string Id,

    /// <summary>Human-readable label, e.g. "5 min", "1 day".</summary>
    string Label,

    /// <summary>
    /// Bybit kline interval string used in the REST API and stored in
    /// the data-service database, e.g. "5", "D".
    /// </summary>
    string BybitInterval,

    /// <summary>Timeframe data class — determines cache TTL and candle-count grid.</summary>
    TimeframeClass Class,

    /// <summary>Duration of one candle in milliseconds.</summary>
    long StepMs
);

/// <summary>
/// Source of truth for all supported timeframes.
/// Bybit kline intervals: 1 3 5 15 30 60 120 240 360 720 D
/// External (client-facing) ids use the "Xm" / "1d" convention.
/// </summary>
public static class TimeframeMap
{
    private static readonly TimeframeInfo[] _all =
    [
        new("1m",   "1 min",    "1",   TimeframeClass.Heavy,  60_000L),
        new("3m",   "3 min",    "3",   TimeframeClass.Heavy,  180_000L),
        new("5m",   "5 min",    "5",   TimeframeClass.Heavy,  300_000L),
        new("15m",  "15 min",   "15",  TimeframeClass.Medium, 900_000L),
        new("30m",  "30 min",   "30",  TimeframeClass.Medium, 1_800_000L),
        new("60m",  "1 hour",   "60",  TimeframeClass.Medium, 3_600_000L),
        new("120m", "2 hours",  "120", TimeframeClass.Medium, 7_200_000L),
        new("240m", "4 hours",  "240", TimeframeClass.Medium, 14_400_000L),
        new("360m", "6 hours",  "360", TimeframeClass.Light,  21_600_000L),
        new("720m", "12 hours", "720", TimeframeClass.Light,  43_200_000L),
        new("1d",   "1 day",    "D",   TimeframeClass.Light,  86_400_000L),
    ];

    /// <summary>All supported timeframes in display order.</summary>
    public static IReadOnlyList<TimeframeInfo> All => _all;

    private static readonly Dictionary<string, TimeframeInfo> _byId =
        _all.ToDictionary(t => t.Id, StringComparer.OrdinalIgnoreCase);

    // Reverse lookup: bybit kline interval ("60", "D") -> client id ("60m", "1d").
    // Used by data-service callers to derive the canonical table-name suffix
    // (data-service tables use the client-id key, e.g. btcusdt_60m).
    private static readonly Dictionary<string, TimeframeInfo> _byBybit =
        _all.ToDictionary(t => t.BybitInterval, StringComparer.OrdinalIgnoreCase);

    /// <summary>Returns true if <paramref name="id"/> is a supported timeframe id.</summary>
    public static bool IsValid(string id) => _byId.ContainsKey(id);

    /// <summary>
    /// Looks up by client-facing id (case-insensitive).
    /// Returns false if the id is not recognised.
    /// </summary>
    public static bool TryGetById(string id, [System.Diagnostics.CodeAnalysis.NotNullWhen(true)] out TimeframeInfo? info) =>
        _byId.TryGetValue(id, out info);

    /// <summary>
    /// Looks up by client-facing id (case-insensitive).
    /// Throws <see cref="ArgumentException"/> for unknown ids.
    /// </summary>
    public static TimeframeInfo GetById(string id) =>
        _byId.TryGetValue(id, out var info)
            ? info
            : throw new ArgumentException($"Unknown timeframe: '{id}'", nameof(id));

    /// <summary>
    /// Converts the client-facing id (e.g. "5m") to the Bybit kline interval
    /// string (e.g. "5") used in REST requests and data-service commands.
    /// </summary>
    public static string ToBybitInterval(string id) => GetById(id).BybitInterval;

    /// <summary>
    /// Reverse mapping: converts a Bybit kline interval (e.g. "60", "D") to
    /// the client-facing id (e.g. "60m", "1d") used as the data-service
    /// timeframe key — the same key data-service appends to OHLCV table
    /// names (e.g. <c>btcusdt_60m</c>). Returns false if the value is not
    /// a known Bybit interval.
    /// </summary>
    public static bool TryGetByBybitInterval(
        string bybitInterval,
        [System.Diagnostics.CodeAnalysis.NotNullWhen(true)] out TimeframeInfo? info) =>
        _byBybit.TryGetValue(bybitInterval, out info);

    /// <summary>
    /// Reverse mapping helper: returns the client-facing timeframe id for
    /// the given Bybit kline interval, or the input itself when the value
    /// already looks like a client id (defensive fallback to keep callers
    /// resilient to mixed inputs upstream).
    /// </summary>
    public static string BybitIntervalToClientId(string bybitInterval)
    {
        if (string.IsNullOrEmpty(bybitInterval)) return bybitInterval;
        if (_byBybit.TryGetValue(bybitInterval, out var byBybit)) return byBybit.Id;
        if (_byId.ContainsKey(bybitInterval)) return bybitInterval; // already a client id
        return bybitInterval;
    }
}
