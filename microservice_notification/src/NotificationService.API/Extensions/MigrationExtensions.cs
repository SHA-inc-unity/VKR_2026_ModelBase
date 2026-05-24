using System.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Storage;
using NotificationService.Infrastructure.Data;

namespace NotificationService.API.Extensions;

public static class MigrationExtensions
{
    private static readonly string[] RequiredTables = ["notifications", "notification_settings"];

    public static async Task MigrateAndSeedAsync(this WebApplication app)
    {
        using var scope = app.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<NotificationDbContext>();
        var logger = scope.ServiceProvider.GetRequiredService<ILogger<NotificationDbContext>>();

        try
        {
            var pending = db.Database.GetPendingMigrations().ToArray();
            logger.LogInformation(
                "Notification EF pending migrations: {Pending}",
                pending.Length == 0 ? "<none>" : string.Join(", ", pending));

            await db.Database.MigrateAsync();
            await EnsureCoreSchemaAsync(db, logger);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Notification DB migration failed");
            throw;
        }
    }

    private static async Task EnsureCoreSchemaAsync(NotificationDbContext db, ILogger logger)
    {
        var existing = await GetExistingPublicTablesAsync(db);
        var missing = RequiredTables.Where(t => !existing.Contains(t)).ToArray();
        if (missing.Length == 0) return;

        if (missing.Length != RequiredTables.Length)
            throw new InvalidOperationException(
                $"Notification DB schema is partial; missing: {string.Join(", ", missing)}");

        logger.LogWarning("Notification DB has no tables after migration; recreating from model");
        var creator = db.GetService<IRelationalDatabaseCreator>();
        await creator.CreateTablesAsync();
    }

    private static async Task<HashSet<string>> GetExistingPublicTablesAsync(NotificationDbContext db)
    {
        var tables = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var conn = db.Database.GetDbConnection();
        var shouldClose = conn.State != ConnectionState.Open;
        if (shouldClose) await conn.OpenAsync();
        try
        {
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = "select table_name from information_schema.tables where table_schema = 'public';";
            await using var reader = await cmd.ExecuteReaderAsync();
            while (await reader.ReadAsync())
            {
                if (!reader.IsDBNull(0)) tables.Add(reader.GetString(0));
            }
        }
        finally
        {
            if (shouldClose) await conn.CloseAsync();
        }
        return tables;
    }
}
