namespace AccountService.Domain.Entities;

public class UserRole
{
    public Guid UserId { get; private set; }
    public int RoleId { get; private set; }

    public User User { get; private set; } = null!;
    public Role Role { get; private set; } = null!;

    private UserRole() { }

    public static UserRole Create(Guid userId, int roleId) =>
        new() { UserId = userId, RoleId = roleId };
}
