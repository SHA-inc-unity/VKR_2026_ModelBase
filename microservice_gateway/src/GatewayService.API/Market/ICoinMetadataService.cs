namespace GatewayService.API.Market;

/// <summary>
/// Per-coin supply / valuation metadata sourced from CoinGecko
/// <c>/coins/markets</c> for the gateway's curated tracked universe.
///
/// <para>
/// Used to compute a <b>real</b> circulating-supply-based market cap
/// (<c>circulatingSupply × livePrice</c>) instead of the old open-interest /
/// turnover proxy. Cached for several hours (supply numbers move slowly) with a
/// soft-fail to an empty map so a CoinGecko outage never breaks the snapshot.
/// </para>
/// </summary>
public interface ICoinMetadataService
{
    /// <summary>
    /// Returns the (cached) base-asset → metadata map for the curated universe.
    /// Bases that are unmapped or that CoinGecko did not return are simply absent
    /// from the dictionary (callers treat the miss as "unknown supply").
    /// On a fetch failure returns an empty (or last-good) map — never throws.
    /// </summary>
    Task<IReadOnlyDictionary<string, CoinMetadata>> GetMetadataAsync(CancellationToken ct = default);
}

/// <summary>
/// Slow-moving per-coin supply / all-time-high facts. All fields are nullable —
/// CoinGecko reports <c>null</c> for coins with no fixed cap (e.g. no max supply)
/// or no measured ATH, and we preserve that distinction rather than coercing to 0.
/// </summary>
public sealed record CoinMetadata(
    decimal? CirculatingSupply,
    decimal? TotalSupply,
    decimal? MaxSupply,
    decimal? Ath);
