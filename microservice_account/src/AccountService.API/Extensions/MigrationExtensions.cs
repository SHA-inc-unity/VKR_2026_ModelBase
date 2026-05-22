using AccountService.Infrastructure.Data;
using AccountService.Application.Interfaces.Services;
using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace AccountService.API.Extensions;

public static class MigrationExtensions
{
    private const string DefaultAdminEmail = "admin@modelline.local";
    private const string DefaultAdminUsername = "admin";
    private const string DefaultAdminPassword = "admin";

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
        var configuredEmail = configuration["AdminBootstrap:Email"]?.Trim();
        var configuredUsername = configuration["AdminBootstrap:Username"]?.Trim();
        var configuredPassword = configuration["AdminBootstrap:Password"];
        var useDefaultBootstrap = string.IsNullOrWhiteSpace(configuredEmail)
            && string.IsNullOrWhiteSpace(configuredUsername)
            && string.IsNullOrWhiteSpace(configuredPassword);

        if (!useDefaultBootstrap &&
            (string.IsNullOrWhiteSpace(configuredEmail) || string.IsNullOrWhiteSpace(configuredUsername) || string.IsNullOrWhiteSpace(configuredPassword)))
        {
            logger.LogInformation("Admin bootstrap account is partially configured; skipping admin user seed");
            return;
        }

        var email = useDefaultBootstrap ? DefaultAdminEmail : configuredEmail!;
        var username = useDefaultBootstrap ? DefaultAdminUsername : configuredUsername!;
        var password = useDefaultBootstrap ? DefaultAdminPassword : configuredPassword!;

        var strengthError = passwordService.ValidateStrength(password);
        if (strengthError is not null && !useDefaultBootstrap)
        {
            throw new InvalidOperationException($"Admin bootstrap password is too weak: {strengthError}");
        }

        if (strengthError is not null && useDefaultBootstrap)
        {
            logger.LogWarning(
                "Using default bootstrap admin credentials {Username}/{Password}. Override AdminBootstrap:* in production.",
                username,
                password);
        }

        var adminRole = await db.Roles.AsNoTracking().FirstOrDefaultAsync(r => r.Code == Role.Codes.Admin)
            ?? throw new InvalidOperationException("Admin role was not seeded.");

        var normalizedEmail = email.ToLowerInvariant();
        var normalizedUsername = username.Trim();
        var user = await db.Users.FirstOrDefaultAsync(u => u.Username == normalizedUsername);
        if (user is null)
        {
            user = await db.Users.FirstOrDefaultAsync(u => u.Email == normalizedEmail);
        }

        if (user is null)
        {
            user = User.Create(email, normalizedUsername, passwordService.Hash(password));
            await db.Users.AddAsync(user);
            await db.UserSettings.AddAsync(UserSettings.CreateDefault(user.Id));
            await db.UserRoles.AddAsync(UserRole.Create(user.Id, adminRole.Id));
            await db.SaveChangesAsync();
            logger.LogInformation("Bootstrap admin user {UserId} created", user.Id);
            return;
        }

        if (!string.Equals(user.Username, normalizedUsername, StringComparison.Ordinal))
        {
            user.UpdateProfile(normalizedUsername);
            await db.SaveChangesAsync();
            logger.LogInformation("Bootstrap admin username {Username} assigned to existing user {UserId}", normalizedUsername, user.Id);
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
