namespace GatewayService.API.DTOs.Responses;

// ── Market config ─────────────────────────────────────────────────────────────

/// <summary>
/// Response body for GET /api/v1/market/config.
/// Server-authoritative source of allowed symbols, timeframes and candle limits.
/// Kotlin clients must read this before building any chart requests.
/// </summary>
public sealed record MarketConfigResponse
{
    /// <summary>Sorted list of active tradeable USDT-perpetual symbols.</summary>
    public IReadOnlyList<string> Symbols { get; init; } = [];

    /// <summary>All supported timeframes in display order.</summary>
    public IReadOnlyList<TimeframeDto> Timeframes { get; init; } = [];

    /// <summary>
    /// Allowed candle counts grouped by timeframe class.
    /// Clients must validate their chosen limit against the class of the
    /// chosen timeframe before sending a chart request.
    /// </summary>
    public CandleCountConstraintsDto CandleCounts { get; init; } = new();

    /// <summary>Default values to pre-populate the chart UI.</summary>
    public MarketDefaultsDto Defaults { get; init; } = new();

    /// <summary>When this config response was generated (UTC).</summary>
    public DateTimeOffset CachedAt { get; init; } = DateTimeOffset.UtcNow;

    /// <summary>When the symbol list was last refreshed from Bybit (UTC).</summary>
    public DateTimeOffset SymbolsUpdatedAt { get; init; } = DateTimeOffset.UtcNow;
}

/// <summary>Single timeframe descriptor sent to the client.</summary>
public sealed record TimeframeDto(
    /// <summary>Client-facing identifier, e.g. "5m", "1d".</summary>
    string Id,

    /// <summary>Human-readable label, e.g. "5 min", "1 day".</summary>
    string Label,

    /// <summary>
    /// Timeframe class: "heavy" (1m–5m) | "medium" (15m–240m) | "light" (360m+).
    /// Clients use this to look up the correct candle-count grid.
    /// </summary>
    string Class,

    /// <summary>
    /// Duration of one candle in milliseconds.
    /// Useful for the client to compute chart time axes without hard-coding constants.
    /// </summary>
    int StepMs
);

/// <summary>
/// Allowed candle counts per timeframe class.
/// Clients must pick a value from the correct class array for their chosen timeframe.
/// </summary>
public sealed record CandleCountConstraintsDto
{
    /// <summary>Allowed counts for heavy timeframes (1m, 3m, 5m).</summary>
    public IReadOnlyList<int> Heavy { get; init; } = [];

    /// <summary>Allowed counts for medium timeframes (15m..240m).</summary>
    public IReadOnlyList<int> Medium { get; init; } = [];

    /// <summary>Allowed counts for light timeframes (360m, 720m, 1d).</summary>
    public IReadOnlyList<int> Light { get; init; } = [];

    /// <summary>Timeframe ids that belong to the "heavy" class.</summary>
    public IReadOnlyList<string> HeavyTimeframes { get; init; } = [];

    /// <summary>Timeframe ids that belong to the "medium" class.</summary>
    public IReadOnlyList<string> MediumTimeframes { get; init; } = [];

    /// <summary>Timeframe ids that belong to the "light" class.</summary>
    public IReadOnlyList<string> LightTimeframes { get; init; } = [];
}

/// <summary>Default values to pre-populate the chart UI on first load.</summary>
public sealed record MarketDefaultsDto(
    string Symbol    = "BTCUSDT",
    string Timeframe = "5m",
    int    CandleCount = 200
);
