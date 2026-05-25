using AccountService.Infrastructure.Data;
using AccountService.Application.Interfaces.Services;
using AccountService.Domain.Entities;
using System.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Storage;

namespace AccountService.API.Extensions;

public static class MigrationExtensions
{
    private const string DefaultAdminEmail = "admin@modelline.local";
    private const string DefaultAdminUsername = "admin";
    private const string DefaultAdminPassword = "admin";
    private static readonly string[] RequiredTables =
    [
        "roles",
        "users",
        "user_roles",
        "user_settings",
        "refresh_tokens",
        "audit_login_events",
        "exchange_api_keys",
        "exchange_metadata",
    ];

    public static async Task MigrateAndSeedAsync(this WebApplication app)
    {
        using var scope = app.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AccountDbContext>();
        var passwordService = scope.ServiceProvider.GetRequiredService<IPasswordService>();
        var logger = scope.ServiceProvider.GetRequiredService<ILogger<AccountDbContext>>();

        try
        {
            var availableMigrations = db.Database.GetMigrations().ToArray();
            var pendingMigrations = db.Database.GetPendingMigrations().ToArray();
            logger.LogInformation(
                "Account EF migrations discovered. Available: {AvailableMigrations}. Pending: {PendingMigrations}",
                availableMigrations.Length == 0 ? "<none>" : string.Join(", ", availableMigrations),
                pendingMigrations.Length == 0 ? "<none>" : string.Join(", ", pendingMigrations));

            await db.Database.MigrateAsync();
            logger.LogInformation("Database migration applied successfully");

            await EnsureCoreSchemaAsync(db, logger);

            await EnsureBootstrapAdminAsync(app.Configuration, db, passwordService, logger);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "An error occurred while migrating the database");
            throw;
        }
    }

    private static async Task EnsureCoreSchemaAsync(AccountDbContext db, ILogger logger)
    {
        var existingTables = await GetExistingPublicTablesAsync(db);
        var missingTables = RequiredTables.Where(table => !existingTables.Contains(table)).ToArray();
        if (missingTables.Length == 0)
        {
            return;
        }

        // Fast path: brand-new DB → create the entire model from the snapshot.
        if (missingTables.Length == RequiredTables.Length)
        {
            logger.LogWarning(
                "Account database has no application tables after migration. Recreating schema from current EF model.");

            var creator = db.GetService<IRelationalDatabaseCreator>();
            await creator.CreateTablesAsync();
        }
        else
        {
            // Partial schema: the DB was created before the latest migration was
            // added. EF can't selectively create individual tables from a snapshot
            // without rewinding through migrations, so apply hand-rolled DDL for
            // the new exchange-API tables.
            logger.LogWarning(
                "Account database is missing tables {Missing}. Applying inline DDL for new schema.",
                string.Join(", ", missingTables));
            await EnsureExchangeApiTablesAsync(db, missingTables);
        }

        existingTables = await GetExistingPublicTablesAsync(db);
        missingTables = RequiredTables.Where(table => !existingTables.Contains(table)).ToArray();
        if (missingTables.Length != 0)
        {
            throw new InvalidOperationException(
                $"Account database schema recovery failed. Missing tables after CreateTablesAsync: {string.Join(", ", missingTables)}");
        }

        logger.LogInformation("Account database schema recovered from current EF model.");
    }

    private static async Task EnsureExchangeApiTablesAsync(AccountDbContext db, string[] missingTables)
    {
        if (missingTables.Contains("exchange_api_keys", StringComparer.OrdinalIgnoreCase))
        {
            await db.Database.ExecuteSqlRawAsync(@"
                CREATE TABLE IF NOT EXISTS exchange_api_keys (
                    id uuid PRIMARY KEY,
                    user_id uuid NOT NULL,
                    exchange varchar(32) NOT NULL,
                    label varchar(64) NOT NULL,
                    api_key_enc varchar(1024) NOT NULL,
                    api_secret_enc varchar(2048) NOT NULL,
                    api_key_masked varchar(64) NOT NULL,
                    can_read boolean NOT NULL,
                    can_trade boolean NOT NULL,
                    created_at timestamptz NOT NULL,
                    last_used_at timestamptz NULL,
                    status varchar(20) NOT NULL,
                    last_validation_error varchar(512) NULL,
                    last_validated_at timestamptz NULL,
                    CONSTRAINT fk_exchange_api_keys_users_user_id FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_exchange_api_keys_user_id_exchange
                    ON exchange_api_keys (user_id, exchange);
            ");
        }
        if (missingTables.Contains("exchange_metadata", StringComparer.OrdinalIgnoreCase))
        {
            await db.Database.ExecuteSqlRawAsync(@"
                CREATE TABLE IF NOT EXISTS exchange_metadata (
                    id uuid PRIMARY KEY,
                    exchange varchar(32) NOT NULL,
                    symbol varchar(32) NOT NULL,
                    category varchar(32) NOT NULL,
                    maker_fee_bps numeric NULL,
                    taker_fee_bps numeric NULL,
                    min_notional numeric NULL,
                    max_leverage numeric NULL,
                    raw_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                    captured_at timestamptz NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ix_exchange_metadata_exchange_symbol_category
                    ON exchange_metadata (exchange, symbol, category);
            ");
        }
    }

    private static async Task<HashSet<string>> GetExistingPublicTablesAsync(AccountDbContext db)
    {
        var tables = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var connection = db.Database.GetDbConnection();
        var shouldClose = connection.State != ConnectionState.Open;

        if (shouldClose)
        {
            await connection.OpenAsync();
        }

        try
        {
            await using var command = connection.CreateCommand();
            command.CommandText = "select table_name from information_schema.tables where table_schema = 'public';";

            await using var reader = await command.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                if (!reader.IsDBNull(0))
                {
                    tables.Add(reader.GetString(0));
                }
            }
        }
        finally
        {
            if (shouldClose)
            {
                await connection.CloseAsync();
            }
        }

        return tables;
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
