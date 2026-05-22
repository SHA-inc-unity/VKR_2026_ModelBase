using AccountService.Domain.Enums;

namespace AccountService.Domain.Entities;

public class User
{
    public Guid Id { get; private set; }
    public string Email { get; private set; } = string.Empty;
    public string Username { get; private set; } = string.Empty;
    public string PasswordHash { get; private set; } = string.Empty;
    public UserStatus Status { get; private set; }
    public DateTimeOffset CreatedAt { get; private set; }
    public DateTimeOffset UpdatedAt { get; private set; }

    // Navigation properties
    public UserSettings? Settings { get; private set; }
    public ICollection<UserRole> UserRoles { get; private set; } = [];
    public ICollection<RefreshToken> RefreshTokens { get; private set; } = [];
    public ICollection<AuditLoginEvent> AuditLoginEvents { get; private set; } = [];

    // EF Core
    private User() { }

    public static User Create(string email, string username, string passwordHash)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(email);
        ArgumentException.ThrowIfNullOrWhiteSpace(username);
        ArgumentException.ThrowIfNullOrWhiteSpace(passwordHash);

        var now = DateTimeOffset.UtcNow;
        return new User
        {
            Id = Guid.NewGuid(),
            Email = email.ToLowerInvariant().Trim(),
            Username = username.Trim(),
            PasswordHash = passwordHash,
            Status = UserStatus.Active,
            CreatedAt = now,
            UpdatedAt = now
        };
    }

    public void UpdateProfile(string? username)
    {
        if (!string.IsNullOrWhiteSpace(username))
            Username = username.Trim();
        UpdatedAt = DateTimeOffset.UtcNow;
    }

    public void UpdatePassword(string newPasswordHash)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(newPasswordHash);
        PasswordHash = newPasswordHash;
        UpdatedAt = DateTimeOffset.UtcNow;
    }

    public void Deactivate()
    {
        Status = UserStatus.Inactive;
        UpdatedAt = DateTimeOffset.UtcNow;
    }

    public bool IsActive => Status == UserStatus.Active;
}
