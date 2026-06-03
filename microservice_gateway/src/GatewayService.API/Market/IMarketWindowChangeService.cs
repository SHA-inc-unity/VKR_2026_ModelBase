namespace GatewayService.API.Market;

/// <summary>
/// Multi-window price-change percentages for a single symbol, computed in the
/// gateway from <b>our own</b> candle history (microservice_data) — not from any
/// external API. All fields are nullable: a window stays <c>null</c> when we do
/// not have candle history old enough to anchor it ("show what we have").
///
/// <para>
/// The 24 h change is deliberately NOT part of this record — it keeps coming from
/// the Bybit ticker snapshot (<c>price24hPcnt</c>). This record only adds the
/// 1 h / 7 d / 30 d windows on top of that.
/// </para>
/// </summary>
public sealed record WindowChange(
    decimal? Change1h,
    decimal? Change7d,
    decimal? Change30d)
{
    /// <summary>All-null window — used when no candle history was resolved.</summary>
    public static readonly WindowChange Empty = new(null, null, null);
}

/// <summary>
/// Computes the 1 h / 7 d / 30 d price-change windows for the market snapshot from
/// the gateway's own candle store via the existing <c>cmd.data.dataset.latest_rows</c>
/// Kafka query — no data-service change, no new topic, no external (CoinGecko/Bybit)
/// call.
///
/// <para>
/// Mirrors the lazy, cache-backed, soft-fail contract of
/// <see cref="ICoinMetadataService"/>: the per-snapshot windows map is cached in
/// <see cref="IMarketCacheService"/> for a short TTL, and any failure degrades to an
/// empty map so the snapshot never breaks — affected windows simply render as
/// <c>null</c>. A cache-warm read performs zero Kafka calls.
/// </para>
/// </summary>
public interface IMarketWindowChangeService
{
    /// <summary>
    /// Returns the (cached) gateway-symbol → <see cref="WindowChange"/> map for the
    /// passed-in live prices (the snapshot's current price per symbol, e.g.
    /// <c>BTCUSDT</c> → 100000). Symbols whose candle history is missing/short are
    /// simply absent from the dictionary (callers treat the miss as
    /// <see cref="WindowChange.Empty"/>). On a fetch failure returns an empty (or
    /// last-good) map — never throws.
    /// </summary>
    Task<IReadOnlyDictionary<string, WindowChange>> GetWindowChangesAsync(
        IReadOnlyDictionary<string, decimal> livePriceBySymbol,
        CancellationToken ct);
}
