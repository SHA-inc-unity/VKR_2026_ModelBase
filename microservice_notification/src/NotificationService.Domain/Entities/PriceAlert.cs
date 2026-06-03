namespace NotificationService.Domain.Entities;

/// <summary>
/// A user-defined price alert: fire a notification once a symbol's live price
/// crosses a target in the chosen direction ("above"/"below"). Unlike the
/// favorite-drift watcher (single % threshold per user), this is a discrete
/// per-symbol target rule. The alert is <see cref="IsArmed"/> while waiting to
/// fire; firing disarms it (fire-once), and it re-arms automatically when the
/// price crosses back to the other side of the target.
/// </summary>
public sealed class PriceAlert
{
    public Guid Id { get; private set; }
    public Guid UserId { get; private set; }

    /// <summary>Market symbol (always upper-case, e.g. BTCUSDT).</summary>
    public string Symbol { get; private set; } = string.Empty;

    /// <summary>Trigger direction — "above" or "below" (always lower-case).</summary>
    public string Condition { get; private set; } = string.Empty;

    public decimal TargetPrice { get; private set; }

    /// <summary>User toggle — a disabled alert is never evaluated.</summary>
    public bool IsEnabled { get; private set; }

    /// <summary>
    /// True while the alert is waiting to fire. Cleared on fire (fire-once) and
    /// set again when the price crosses back to the non-triggering side.
    /// </summary>
    public bool IsArmed { get; private set; } = true;

    public DateTime? LastTriggeredAt { get; private set; }
    public decimal? LastObservedPrice { get; private set; }

    public DateTime CreatedAt { get; private set; }
    public DateTime UpdatedAt { get; private set; }

    private PriceAlert() { }

    public static PriceAlert Create(Guid userId, string symbol, string condition, decimal targetPrice, bool isEnabled)
    {
        var normalizedSymbol = NormalizeSymbol(symbol);
        var normalizedCondition = NormalizeCondition(condition);

        var now = DateTime.UtcNow;
        return new PriceAlert
        {
            Id = Guid.NewGuid(),
            UserId = userId,
            Symbol = normalizedSymbol,
            Condition = normalizedCondition,
            TargetPrice = targetPrice,
            IsEnabled = isEnabled,
            IsArmed = true,
            LastTriggeredAt = null,
            LastObservedPrice = null,
            CreatedAt = now,
            UpdatedAt = now,
        };
    }

    /// <summary>
    /// Partial update — only the provided fields change. Re-arms the alert when
    /// the target price or condition changes, or when it is re-enabled, so a
    /// reconfigured (or re-enabled) alert can fire again on the next match.
    /// </summary>
    public void Update(string? symbol, string? condition, decimal? targetPrice, bool? isEnabled)
    {
        var reArm = false;

        if (!string.IsNullOrWhiteSpace(symbol))
            Symbol = NormalizeSymbol(symbol);

        if (!string.IsNullOrWhiteSpace(condition))
        {
            var normalized = NormalizeCondition(condition);
            if (normalized != Condition) reArm = true;
            Condition = normalized;
        }

        if (targetPrice.HasValue)
        {
            if (targetPrice.Value != TargetPrice) reArm = true;
            TargetPrice = targetPrice.Value;
        }

        if (isEnabled.HasValue)
        {
            if (isEnabled.Value && !IsEnabled) reArm = true;
            IsEnabled = isEnabled.Value;
        }

        if (reArm) IsArmed = true;
        UpdatedAt = DateTime.UtcNow;
    }

    /// <summary>Record a fire: disarm (fire-once), stamp the trigger time and the price.</summary>
    public void MarkFired(decimal price)
    {
        IsArmed = false;
        LastTriggeredAt = DateTime.UtcNow;
        LastObservedPrice = price;
        UpdatedAt = DateTime.UtcNow;
    }

    /// <summary>Re-arm after the price crosses back to the non-triggering side.</summary>
    public void ReArm()
    {
        IsArmed = true;
        UpdatedAt = DateTime.UtcNow;
    }

    private static string NormalizeSymbol(string symbol)
    {
        var trimmed = (symbol ?? string.Empty).Trim();
        if (trimmed.Length == 0)
            throw new ArgumentException("Symbol is required", nameof(symbol));
        return trimmed.ToUpperInvariant();
    }

    private static string NormalizeCondition(string condition)
    {
        var normalized = (condition ?? string.Empty).Trim().ToLowerInvariant();
        if (normalized is not ("above" or "below"))
            throw new ArgumentException("Condition must be 'above' or 'below'", nameof(condition));
        return normalized;
    }
}
