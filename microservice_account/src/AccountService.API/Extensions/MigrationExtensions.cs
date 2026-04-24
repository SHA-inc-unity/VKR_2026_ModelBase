using AccountService.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace AccountService.API.Extensions;

public static class MigrationExtensions
{
    public static async Task MigrateAndSeedAsync(this WebApplication app)
    {
        using var scope = app.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<AccountDbContext>();
        var logger = scope.ServiceProvider.GetRequiredService<ILogger<AccountDbContext>>();

        try
        {
            await db.Database.MigrateAsync();
            logger.LogInformation("Database migration applied successfully");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "An error occurred while migrating the database");
            throw;
        }
    }
}
