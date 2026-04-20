namespace AccountService.Domain.Entities;

public class Role
{
    public int Id { get; private set; }
    public string Code { get; private set; } = string.Empty;
    public string Name { get; private set; } = string.Empty;

    public ICollection<UserRole> UserRoles { get; private set; } = [];

    private Role() { }

    public static class Codes
    {
        public const string Admin = "admin";
        public const string User = "user";
    }
}
