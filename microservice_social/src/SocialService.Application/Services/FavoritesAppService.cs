using SocialService.Application.DTOs.Responses;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Application.Interfaces.Services;
using SocialService.Domain.Entities;

namespace SocialService.Application.Services;

public sealed class FavoritesAppService : IFavoritesAppService
{
    private readonly IFavoriteRepository _repo;
    private readonly IEventBus _bus;

    public FavoritesAppService(IFavoriteRepository repo, IEventBus bus)
    {
        _repo = repo;
        _bus = bus;
    }

    public async Task<FavoritesResponse> ListAsync(Guid userId, CancellationToken ct)
    {
        var symbols = await _repo.GetSymbolsAsync(userId, ct);
        return new FavoritesResponse { Symbols = symbols };
    }

    public async Task<bool> AddAsync(Guid userId, string symbol, CancellationToken ct)
    {
        symbol = symbol.Trim().ToUpperInvariant();
        if (string.IsNullOrWhiteSpace(symbol)) return false;

        if (await _repo.ExistsAsync(userId, symbol, ct)) return false;

        await _repo.AddAsync(Favorite.Create(userId, symbol), ct);
        await _bus.PublishAsync("favorite.added", new { userId, symbol }, ct);
        return true;
    }

    public async Task<bool> RemoveAsync(Guid userId, string symbol, CancellationToken ct)
    {
        symbol = symbol.Trim().ToUpperInvariant();
        if (string.IsNullOrWhiteSpace(symbol)) return false;

        var removed = await _repo.RemoveAsync(userId, symbol, ct);
        if (removed)
        {
            await _bus.PublishAsync("favorite.removed", new { userId, symbol }, ct);
        }
        return removed;
    }

    public Task<IReadOnlyList<Guid>> UsersBySymbolAsync(string symbol, CancellationToken ct)
    {
        symbol = symbol.Trim().ToUpperInvariant();
        return _repo.GetUsersBySymbolAsync(symbol, ct);
    }
}
