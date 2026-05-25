using AccountService.Domain.Entities;

namespace AccountService.Application.Interfaces.Repositories;

public interface IExchangeApiKeyRepository
{
    Task<List<ExchangeApiKey>> ListAsync(Guid userId, CancellationToken ct = default);
    Task<ExchangeApiKey?> GetActiveForExchangeAsync(Guid userId, string exchange, CancellationToken ct = default);
    Task<ExchangeApiKey?> GetByIdAsync(Guid userId, Guid id, CancellationToken ct = default);
    Task AddAsync(ExchangeApiKey key, CancellationToken ct = default);
    Task SaveChangesAsync(CancellationToken ct = default);
}
