using AccountService.Application.Interfaces.Cache;

namespace AccountService.Infrastructure.Cache;

/// <summary>No-op implementation — default when Redis is not configured.</summary>
public sealed class NullTokenCacheService : ITokenCacheService
{
    public Task<bool> IsAccessTokenRevokedAsync(string jti, CancellationToken ct = default) =>
        Task.FromResult(false);

    public Task RevokeAccessTokenAsync(string jti, TimeSpan remaining, CancellationToken ct = default) =>
        Task.CompletedTask;
}
