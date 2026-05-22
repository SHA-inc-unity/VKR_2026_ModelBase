using AccountService.Application.Interfaces.Repositories;
using AccountService.Domain.Entities;
using AccountService.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace AccountService.Infrastructure.Repositories;

public sealed class RoleRepository : IRoleRepository
{
    private readonly AccountDbContext _db;

    public RoleRepository(AccountDbContext db) => _db = db;

    public Task<Role?> GetByCodeAsync(string code, CancellationToken ct = default) =>
        _db.Roles.AsNoTracking().FirstOrDefaultAsync(r => r.Code == code, ct);

    public async Task<IReadOnlyList<string>> GetUserRoleCodesAsync(Guid userId, CancellationToken ct = default) =>
        await _db.UserRoles
            .AsNoTracking()
            .Where(ur => ur.UserId == userId)
            .Select(ur => ur.Role.Code)
            .ToListAsync(ct);

    public async Task AssignRoleAsync(Guid userId, string roleCode, CancellationToken ct = default)
    {
        var role = await GetByCodeAsync(roleCode, ct)
            ?? throw new InvalidOperationException($"Role '{roleCode}' not found.");

        var alreadyAssigned = await _db.UserRoles
            .AnyAsync(ur => ur.UserId == userId && ur.RoleId == role.Id, ct);

        if (!alreadyAssigned)
        {
            var userRole = UserRole.Create(userId, role.Id);
            await _db.UserRoles.AddAsync(userRole, ct);
        }
    }
}
