using AccountService.Domain.Entities;

namespace AccountService.Application.Interfaces.Repositories;

public interface IUserRepository
{
    Task<User?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<User?> GetByEmailAsync(string email, CancellationToken ct = default);
    Task<User?> GetByIdWithRolesAsync(Guid id, CancellationToken ct = default);
    Task<bool> EmailExistsAsync(string email, CancellationToken ct = default);
    Task<bool> UsernameExistsAsync(string username, CancellationToken ct = default);
    Task AddAsync(User user, CancellationToken ct = default);
    Task UpdateAsync(User user, CancellationToken ct = default);

    Task<UserSettings?> GetSettingsAsync(Guid userId, CancellationToken ct = default);
    Task AddSettingsAsync(UserSettings settings, CancellationToken ct = default);

    Task SaveChangesAsync(CancellationToken ct = default);
}
