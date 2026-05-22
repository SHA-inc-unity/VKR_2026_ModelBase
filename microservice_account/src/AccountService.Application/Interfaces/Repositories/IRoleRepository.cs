using AccountService.Domain.Entities;

namespace AccountService.Application.Interfaces.Repositories;

public interface IRoleRepository
{
    Task<Role?> GetByCodeAsync(string code, CancellationToken ct = default);
    Task<IReadOnlyList<string>> GetUserRoleCodesAsync(Guid userId, CancellationToken ct = default);
    Task AssignRoleAsync(Guid userId, string roleCode, CancellationToken ct = default);
}
