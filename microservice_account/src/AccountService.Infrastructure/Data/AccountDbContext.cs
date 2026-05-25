using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace AccountService.Infrastructure.Data;

public sealed class AccountDbContext : DbContext
{
    public AccountDbContext(DbContextOptions<AccountDbContext> options) : base(options) { }

    public DbSet<User> Users => Set<User>();
    public DbSet<Role> Roles => Set<Role>();
    public DbSet<UserRole> UserRoles => Set<UserRole>();
    public DbSet<UserSettings> UserSettings => Set<UserSettings>();
    public DbSet<RefreshToken> RefreshTokens => Set<RefreshToken>();
    public DbSet<AuditLoginEvent> AuditLoginEvents => Set<AuditLoginEvent>();
    public DbSet<ExchangeApiKey> ExchangeApiKeys => Set<ExchangeApiKey>();
    public DbSet<ExchangeMetadata> ExchangeMetadata => Set<ExchangeMetadata>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        modelBuilder.ApplyConfigurationsFromAssembly(typeof(AccountDbContext).Assembly);
    }
}
