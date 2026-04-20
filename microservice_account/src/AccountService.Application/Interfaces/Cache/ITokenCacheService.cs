namespace AccountService.Application.Interfaces.Cache;

/// <summary>
/// Extension point for access token revocation (blacklist).
/// Default (no Redis): always returns false — rely on short token TTL.
/// With Redis: stores revoked JTIs until their natural expiry.
/// </summary>
public interface ITokenCacheService
{
    /// <summary>Returns true if the access token JTI has been explicitly revoked.</summary>
    Task<bool> IsAccessTokenRevokedAsync(string jti, CancellationToken ct = default);

    /// <summary>Blacklists an access token JTI until it naturally expires.</summary>
    Task RevokeAccessTokenAsync(string jti, TimeSpan remaining, CancellationToken ct = default);
}
