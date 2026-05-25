using System.Security.Cryptography;
using System.Text;

namespace AccountService.Application.Crypto;

/// <summary>
/// AES-GCM symmetric encryption helper used for at-rest storage of
/// third-party API secrets. The master key is supplied by the host at
/// composition time (typically from the <c>ACCOUNT_API_KEY_MASTER_KEY</c>
/// environment variable, base64, 32 bytes / 256 bits).
///
/// Ciphertext layout (base64): <c>nonce(12) ‖ ciphertext ‖ tag(16)</c>.
/// </summary>
public interface IAesGcmEncryption
{
    string Encrypt(string plaintext);
    string Decrypt(string ciphertextBase64);
}

public sealed class AesGcmEncryption : IAesGcmEncryption
{
    private const int NonceSize = 12;
    private const int TagSize = 16;

    private readonly byte[] _masterKey;

    /// <param name="masterKeyMaterial">
    /// Either a base64-encoded 32-byte key, or any string the caller wants
    /// SHA-256-derived into a 256-bit key. May be null/empty — a dev-only
    /// fallback is used in that case (callers should warn).
    /// </param>
    public AesGcmEncryption(string? masterKeyMaterial)
    {
        var raw = string.IsNullOrWhiteSpace(masterKeyMaterial)
            ? "dev-only-account-service-master-key-do-not-use-in-prod"
            : masterKeyMaterial;

        _masterKey = DeriveKey(raw);
    }

    private static byte[] DeriveKey(string raw)
    {
        try
        {
            var decoded = Convert.FromBase64String(raw);
            if (decoded.Length == 32) return decoded;
        }
        catch (FormatException) { /* fall through */ }

        return SHA256.HashData(Encoding.UTF8.GetBytes(raw));
    }

    public string Encrypt(string plaintext)
    {
        if (string.IsNullOrEmpty(plaintext)) return string.Empty;

        var nonce = RandomNumberGenerator.GetBytes(NonceSize);
        var plain = Encoding.UTF8.GetBytes(plaintext);
        var cipher = new byte[plain.Length];
        var tag = new byte[TagSize];

        using var gcm = new AesGcm(_masterKey, TagSize);
        gcm.Encrypt(nonce, plain, cipher, tag);

        var combined = new byte[nonce.Length + cipher.Length + tag.Length];
        Buffer.BlockCopy(nonce, 0, combined, 0, nonce.Length);
        Buffer.BlockCopy(cipher, 0, combined, nonce.Length, cipher.Length);
        Buffer.BlockCopy(tag, 0, combined, nonce.Length + cipher.Length, tag.Length);
        return Convert.ToBase64String(combined);
    }

    public string Decrypt(string ciphertextBase64)
    {
        if (string.IsNullOrEmpty(ciphertextBase64)) return string.Empty;

        var combined = Convert.FromBase64String(ciphertextBase64);
        if (combined.Length < NonceSize + TagSize)
            throw new CryptographicException("Ciphertext is too short");

        var nonce = new byte[NonceSize];
        var tag = new byte[TagSize];
        var cipher = new byte[combined.Length - NonceSize - TagSize];

        Buffer.BlockCopy(combined, 0, nonce, 0, NonceSize);
        Buffer.BlockCopy(combined, NonceSize, cipher, 0, cipher.Length);
        Buffer.BlockCopy(combined, NonceSize + cipher.Length, tag, 0, TagSize);

        var plain = new byte[cipher.Length];
        using var gcm = new AesGcm(_masterKey, TagSize);
        gcm.Decrypt(nonce, cipher, tag, plain);

        return Encoding.UTF8.GetString(plain);
    }
}
