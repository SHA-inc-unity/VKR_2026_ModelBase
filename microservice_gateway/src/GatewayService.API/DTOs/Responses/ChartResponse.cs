namespace GatewayService.API.DTOs.Responses;

// ── Chart response ────────────────────────────────────────────────────────────

/// <summary>
/// Response body for GET /api/v1/market/chart.
///
/// Status semantics:
/// - "ok"      — all requested candles are present and returned.
/// - "partial" — some candles returned; background ingest triggered for the rest.
/// - "pending" — no data available yet; ingest triggered; client should retry.
/// - "error"   — request validation error (never reaches the data layer).
///
/// Coverage semantics (meta.coverage):
/// - "full"    — data-service reports ≥99 % coverage for the requested window.
/// - "partial" — data-service has some rows but below the full threshold.
/// - "pending" — no rows exist; ingest has been triggered.
/// - "empty"   — data-service has no record of this symbol/timeframe at all.
/// </summary>
public sealed record ChartResponse
{
    public string Symbol    { get; init; } = string.Empty;
    public string Timeframe { get; init; } = string.Empty;

    /// <summary>The limit that was requested (not the number of candles returned).</summary>
    public int Limit { get; init; }

    /// <summary>OHLCV candles in ascending time order.</summary>
    public IReadOnlyList<CandleDto> Candles { get; init; } = [];

    /// <summary>Request metadata and coverage information.</summary>
    public ChartMetaDto Meta { get; init; } = new();

    /// <summary>
    /// "ok" | "partial" | "pending" — see class documentation.
    /// </summary>
    public string Status { get; init; } = "ok";

    /// <summary>
    /// Milliseconds the client should wait before retrying a "partial" or "pending" response.
    /// Null (omitted from JSON) when status is "ok".
    /// </summary>
    public int? RetryAfterMs { get; init; }
}

/// <summary>A single OHLCV candle in the Kotlin-friendly compact format.</summary>
public sealed record CandleDto(
    /// <summary>Open timestamp (Unix ms, UTC).</summary>
    long    T,
    decimal O,
    decimal H,
    decimal L,
    decimal C,
    decimal V,
    /// <summary>Turnover (quote-currency volume).</summary>
    decimal Tv
);

/// <summary>Metadata attached to every chart response.</summary>
public sealed record ChartMetaDto
{
    /// <summary>How many candles were requested.</summary>
    public int Requested { get; init; }

    /// <summary>How many candles are in this response (may be less than requested).</summary>
    public int Available { get; init; }

    /// <summary>Timestamp of the oldest candle (ms UTC). 0 when no candles returned.</summary>
    public long FromMs { get; init; }

    /// <summary>Timestamp of the newest candle (ms UTC). 0 when no candles returned.</summary>
    public long ToMs { get; init; }

    /// <summary>
    /// "full" | "partial" | "pending" | "empty" — see ChartResponse class docs.
    /// </summary>
    public string Coverage { get; init; } = "full";
}
