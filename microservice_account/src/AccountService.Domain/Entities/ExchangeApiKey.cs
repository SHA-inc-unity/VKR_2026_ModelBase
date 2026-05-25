namespace AccountService.Domain.Entities;

/// <summary>
/// Encrypted API key for a third-party exchange (Bybit, Binance, ...).
/// Cleartext apiKey/apiSecret are never stored — only AES-GCM ciphertext
/// (see <c>AesGcmEncryption</c>) plus a masked preview ("AB••••YZ") that
/// is safe to render in the UI.
/// </summary>
public class ExchangeApiKey
{
    public Guid Id { get; set; }
    public Guid UserId { get; set; }

    /// <summary>"bybit" | "binance" — lower-case slug.</summary>
    public string Exchange { get; set; } = string.Empty;

    /// <summary>User-supplied label, e.g. "main", "scalp", "savings".</summary>
    public string Label { get; set; } = string.Empty;

    /// <summary>AES-GCM(base64) ciphertext of the API key.</summary>
    public string ApiKeyEnc { get; set; } = string.Empty;

    /// <summary>AES-GCM(base64) ciphertext of the API secret.</summary>
    public string ApiSecretEnc { get; set; } = string.Empty;

    /// <summary>UI-safe preview, e.g. "ABCD••••WXYZ".</summary>
    public string ApiKeyMasked { get; set; } = string.Empty;

    public bool CanRead { get; set; } = true;

    /// <summary>Schema column already present so we can add trade flow later
    /// without migrations. The UI currently refuses to set this to true.</summary>
    public bool CanTrade { get; set; } = false;

    public DateTime CreatedAt { get; set; }
    public DateTime? LastUsedAt { get; set; }

    /// <summary>"active" | "revoked".</summary>
    public string Status { get; set; } = "active";

    /// <summary>Cached upstream validation error so we don't re-probe the
    /// exchange on every page load.</summary>
    public string? LastValidationError { get; set; }
    public DateTime? LastValidatedAt { get; set; }
}
