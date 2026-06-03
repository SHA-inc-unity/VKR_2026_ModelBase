using AccountService.Application.Interfaces.Cache;
using Microsoft.Extensions.Logging;
using StackExchange.Redis;

namespace AccountService.Infrastructure.Cache;

/// <summary>
/// Redis-backed access-token blacklist. Registered when REDIS_URL is configured.
/// All operations are <b>fail-soft</b>: the denylist is a best-effort enhancement on
/// top of normal token expiry, so a Redis outage must never lock users out (reads
/// fail open → "not revoked") or break logout (writes are swallowed). The access
/// token still expires on its own, and refresh-token revocation is durable in Postgres.
/// </summary>
public sealed class RedisTokenCacheService : ITokenCacheService
{
    private readonly IConnectionMultiplexer _redis;
    private readonly ILogger<RedisTokenCacheService> _log;

    public RedisTokenCacheService(IConnectionMultiplexer redis, ILogger<RedisTokenCacheService> log)
    {
        _redis = redis;
        _log = log;
    }

    public async Task<bool> IsAccessTokenRevokedAsync(string jti, CancellationToken ct = default)
    {
        try
        {
            var db = _redis.GetDatabase();
            return await db.KeyExistsAsync(RevokedKey(jti));
        }
        catch (Exception ex)
        {
            // Fail OPEN: if the denylist is unreachable, do not lock the user out.
            _log.LogWarning(ex,
                "Redis revocation check failed for jti {Jti}; treating token as not revoked", jti);
            return false;
        }
    }

    public async Task RevokeAccessTokenAsync(string jti, TimeSpan remaining, CancellationToken ct = default)
    {
        if (remaining <= TimeSpan.Zero) return;
        try
        {
            var db = _redis.GetDatabase();
            await db.StringSetAsync(RevokedKey(jti), "1", remaining);
        }
        catch (Exception ex)
        {
            // Fail SOFT: logout must succeed even if blacklisting fails. The refresh
            // token is revoked durably in Postgres and the access token still expires.
            _log.LogWarning(ex,
                "Redis revocation write failed for jti {Jti}; access token not blacklisted", jti);
        }
    }

    private static string RevokedKey(string jti) => $"revoked_token:{jti}";
}
