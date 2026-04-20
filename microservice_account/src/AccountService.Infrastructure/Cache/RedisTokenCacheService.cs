using AccountService.Application.Interfaces.Cache;
using StackExchange.Redis;

namespace AccountService.Infrastructure.Cache;

/// <summary>Redis-backed access token blacklist. Registered when REDIS_URL is configured.</summary>
public sealed class RedisTokenCacheService : ITokenCacheService
{
    private readonly IConnectionMultiplexer _redis;

    public RedisTokenCacheService(IConnectionMultiplexer redis) => _redis = redis;

    public async Task<bool> IsAccessTokenRevokedAsync(string jti, CancellationToken ct = default)
    {
        var db = _redis.GetDatabase();
        return await db.KeyExistsAsync(RevokedKey(jti));
    }

    public async Task RevokeAccessTokenAsync(string jti, TimeSpan remaining, CancellationToken ct = default)
    {
        if (remaining <= TimeSpan.Zero) return;
        var db = _redis.GetDatabase();
        await db.StringSetAsync(RevokedKey(jti), "1", remaining);
    }

    private static string RevokedKey(string jti) => $"revoked_token:{jti}";
}
