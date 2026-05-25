namespace AccountService.Domain.Entities;

/// <summary>
/// Static-ish exchange metadata — instrument list, fee tiers, max leverage.
/// Refreshed by a hosted service on the gateway side every few hours.
/// Symbol = "*" means the row applies to the whole exchange (e.g. default fee).
/// </summary>
public class ExchangeMetadata
{
    public Guid Id { get; set; }
    public string Exchange { get; set; } = string.Empty;
    public string Symbol { get; set; } = "*";
    public string Category { get; set; } = string.Empty; // spot | linear | fee_rate

    /// <summary>Basis points — 10 bps = 0.10 %.</summary>
    public decimal? MakerFeeBps { get; set; }
    public decimal? TakerFeeBps { get; set; }
    public decimal? MinNotional { get; set; }
    public decimal? MaxLeverage { get; set; }

    /// <summary>Raw upstream JSON snapshot for forward-compat.</summary>
    public string RawJson { get; set; } = "{}";
    public DateTime CapturedAt { get; set; }
}
