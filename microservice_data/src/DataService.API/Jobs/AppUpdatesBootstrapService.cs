using DataService.API.Database;

namespace DataService.API.Jobs;

/// <summary>
/// Ensures the append-only app-updates schema (app_update_release /
/// app_update_change + the immutability trigger guard) exists and is seeded
/// shortly after startup, without blocking host startup. Uses the same
/// exponential-backoff retry as <see cref="DatasetJobRunner"/>'s schema
/// bootstrap so a cold Postgres doesn't crash the service — it simply retries
/// until the database is reachable.
/// </summary>
public sealed class AppUpdatesBootstrapService : BackgroundService
{
    private readonly AppUpdatesRepository _appUpdates;
    private readonly ILogger<AppUpdatesBootstrapService> _log;

    public AppUpdatesBootstrapService(
        AppUpdatesRepository appUpdates,
        ILogger<AppUpdatesBootstrapService> log)
    {
        _appUpdates = appUpdates;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        // Yield so we don't hold up host startup before the first await.
        await Task.Yield();

        var attempt = 0;
        while (!stoppingToken.IsCancellationRequested && !_appUpdates.SchemaReady)
        {
            try
            {
                await _appUpdates.EnsureSchemaAsync(stoppingToken);
                _log.LogInformation("App-updates schema bootstrap complete");
                return;
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                attempt++;
                var delay = TimeSpan.FromSeconds(Math.Min(30, Math.Pow(2, Math.Min(attempt, 5))));
                _log.LogWarning(ex,
                    "App-updates schema bootstrap failed (attempt {Attempt}); retrying in {Delay}s",
                    attempt, delay.TotalSeconds);
                try { await Task.Delay(delay, stoppingToken); }
                catch (OperationCanceledException) { return; }
            }
        }
    }
}
