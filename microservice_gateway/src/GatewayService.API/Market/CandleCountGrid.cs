namespace GatewayService.API.Market;

/// <summary>
/// Server-authoritative grid of allowed candle counts per timeframe class.
///
/// Design rationale:
/// - Values are discrete to maximise cache key reuse across many clients
///   requesting the same popular combination of symbol + timeframe + limit.
/// - Heavy timeframes (1m–5m) have a tighter cap to protect the data-service
///   from expensive large-window queries on multi-million-row tables.
/// - The grid is sent to clients via /api/v1/market/config so that Kotlin
///   clients never hard-code limits — they only show what the server allows.
/// </summary>
public static class CandleCountGrid
{
    /// <summary>Allowed counts for heavy timeframes (1m, 3m, 5m).</summary>
    public static readonly IReadOnlyList<int> Heavy = [50, 100, 200, 500];

    /// <summary>Allowed counts for medium timeframes (15m..240m).</summary>
    public static readonly IReadOnlyList<int> Medium = [50, 100, 200, 500, 1000];

    /// <summary>Allowed counts for light timeframes (360m, 720m, 1d).</summary>
    public static readonly IReadOnlyList<int> Light = [50, 100, 200, 500, 1000, 2000];

    /// <summary>Returns the allowed counts for the given timeframe class.</summary>
    public static IReadOnlyList<int> ForClass(TimeframeClass cls) => cls switch
    {
        TimeframeClass.Heavy  => Heavy,
        TimeframeClass.Medium => Medium,
        TimeframeClass.Light  => Light,
        _                     => Medium,
    };

    /// <summary>
    /// Returns true when <paramref name="count"/> is in the grid for
    /// <paramref name="cls"/>.
    /// </summary>
    public static bool IsValid(int count, TimeframeClass cls) =>
        ForClass(cls).Contains(count);

    /// <summary>Maximum candle count allowed for <paramref name="cls"/>.</summary>
    public static int MaxFor(TimeframeClass cls) => ForClass(cls)[^1];
}
