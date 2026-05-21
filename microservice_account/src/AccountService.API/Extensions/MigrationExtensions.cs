using AccountService.Infrastructure.Data;
using AccountService.Application.Interfaces.Services;
using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace AccountService.API.Extensions;

public static class MigrationExtensions
{
    public static async Task MigrateAndSeedAsync(this WebApplication app)
    {
        using var scope = app.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AccountDbContext>();
        var passwordService = scope.ServiceProvider.GetRequiredService<IPasswordService>();
        var logger = scope.ServiceProvider.GetRequiredService<ILogger<AccountDbContext>>();

        try
        {
            await db.Database.MigrateAsync();
            logger.LogInformation("Database migration applied successfully");

            await EnsureBootstrapAdminAsync(app.Configuration, db, passwordService, logger);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "An error occurred while migrating the database");
            throw;
        }
    }

    private static async Task EnsureBootstrapAdminAsync(
        IConfiguration configuration,
        AccountDbContext db,
        IPasswordService passwordService,
        ILogger logger)
    {
        var email = configuration["AdminBootstrap:Email"]?.Trim();
        var username = configuration["AdminBootstrap:Username"]?.Trim();
        var password = configuration["AdminBootstrap:Password"];

        if (string.IsNullOrWhiteSpace(email) || string.IsNullOrWhiteSpace(username) || string.IsNullOrWhiteSpace(password))
        {
            logger.LogInformation("Admin bootstrap account is not configured; skipping admin user seed");
            return;
        }

        var strengthError = passwordService.ValidateStrength(password);
        if (strengthError is not null)
        {
            throw new InvalidOperationException($"Admin bootstrap password is too weak: {strengthError}");
        }

        var adminRole = await db.Roles.AsNoTracking().FirstOrDefaultAsync(r => r.Code == Role.Codes.Admin)
            ?? throw new InvalidOperationException("Admin role was not seeded.");

        var normalizedEmail = email.ToLowerInvariant();
        var user = await db.Users.FirstOrDefaultAsync(u => u.Email == normalizedEmail);
        if (user is null)
        {
            user = User.Create(email, username, passwordService.Hash(password));
            await db.Users.AddAsync(user);
            await db.UserSettings.AddAsync(UserSettings.CreateDefault(user.Id));
            await db.UserRoles.AddAsync(UserRole.Create(user.Id, adminRole.Id));
            await db.SaveChangesAsync();
            logger.LogInformation("Bootstrap admin user {UserId} created", user.Id);
            return;
        }

        var hasAdminRole = await db.UserRoles.AnyAsync(ur => ur.UserId == user.Id && ur.RoleId == adminRole.Id);
        if (!hasAdminRole)
        {
            await db.UserRoles.AddAsync(UserRole.Create(user.Id, adminRole.Id));
            await db.SaveChangesAsync();
            logger.LogInformation("Bootstrap admin role assigned to existing user {UserId}", user.Id);
        }
    }
}
