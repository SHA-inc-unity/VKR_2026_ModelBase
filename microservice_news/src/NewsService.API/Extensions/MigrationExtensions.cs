using System.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Storage;
using NewsService.Infrastructure.Data;

namespace NewsService.API.Extensions;

public static class MigrationExtensions
{
    private static readonly string[] RequiredTables = ["news_articles"];

    public static async Task MigrateAndSeedAsync(this WebApplication app)
    {
        using var scope = app.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<NewsDbContext>();
        var logger = scope.ServiceProvider.GetRequiredService<ILogger<NewsDbContext>>();

        try
        {
            var pending = db.Database.GetPendingMigrations().ToArray();
            logger.LogInformation(
                "News EF pending migrations: {Pending}",
                pending.Length == 0 ? "<none>" : string.Join(", ", pending));

            await db.Database.MigrateAsync();
            await EnsureCoreSchemaAsync(db, logger);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "News DB migration failed");
            throw;
        }
    }

    private static async Task EnsureCoreSchemaAsync(NewsDbContext db, ILogger logger)
    {
        var existing = await GetExistingPublicTablesAsync(db);
        var missing = RequiredTables.Where(t => !existing.Contains(t)).ToArray();
        if (missing.Length == 0) return;

        if (missing.Length != RequiredTables.Length)
            throw new InvalidOperationException(
                $"News DB schema is partial; missing: {string.Join(", ", missing)}");

        logger.LogWarning("News DB has no tables after migration; recreating from model");
        var creator = db.GetService<IRelationalDatabaseCreator>();
        await creator.CreateTablesAsync();
    }

    private static async Task<HashSet<string>> GetExistingPublicTablesAsync(NewsDbContext db)
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
