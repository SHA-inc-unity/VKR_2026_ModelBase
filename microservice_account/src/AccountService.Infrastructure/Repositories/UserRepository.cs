using AccountService.Application.Interfaces.Repositories;
using AccountService.Domain.Entities;
using AccountService.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace AccountService.Infrastructure.Repositories;

public sealed class UserRepository : IUserRepository
{
    private readonly AccountDbContext _db;

    public UserRepository(AccountDbContext db) => _db = db;

    // GetByIdAsync stays tracked because callers (UpdateProfileAsync,
    // UpdateSettingsAsync) mutate the returned entity and rely on
    // SaveChangesAsync to persist the change. Switching this to no-tracking
    // would silently drop those updates.
    public Task<User?> GetByIdAsync(Guid id, CancellationToken ct = default) =>
        _db.Users.FirstOrDefaultAsync(u => u.Id == id, ct);

    public Task<User?> GetByEmailAsync(string email, CancellationToken ct = default) =>
        _db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Email == email.ToLowerInvariant(), ct);

    public Task<User?> GetByIdWithRolesAsync(Guid id, CancellationToken ct = default) =>
        _db.Users
            .AsNoTracking()
            .AsSplitQuery()
            .Include(u => u.UserRoles)
            .ThenInclude(ur => ur.Role)
            .FirstOrDefaultAsync(u => u.Id == id, ct);

    public Task<bool> EmailExistsAsync(string email, CancellationToken ct = default) =>
        _db.Users.AsNoTracking().AnyAsync(u => u.Email == email.ToLowerInvariant(), ct);

    public Task<bool> UsernameExistsAsync(string username, CancellationToken ct = default) =>
        _db.Users.AsNoTracking().AnyAsync(u => u.Username == username, ct);

    public async Task AddAsync(User user, CancellationToken ct = default) =>
        await _db.Users.AddAsync(user, ct);

    public Task UpdateAsync(User user, CancellationToken ct = default)
    {
        _db.Users.Update(user);
        return Task.CompletedTask;
    }

    // Settings are returned tracked because UpdateSettingsAsync mutates the
    // entity and relies on EF's change-tracker to flush the update.
    public Task<UserSettings?> GetSettingsAsync(Guid userId, CancellationToken ct = default) =>
        _db.UserSettings.FirstOrDefaultAsync(s => s.UserId == userId, ct);

    public async Task AddSettingsAsync(UserSettings settings, CancellationToken ct = default) =>
        await _db.UserSettings.AddAsync(settings, ct);

    public Task SaveChangesAsync(CancellationToken ct = default) =>
        _db.SaveChangesAsync(ct);
}
