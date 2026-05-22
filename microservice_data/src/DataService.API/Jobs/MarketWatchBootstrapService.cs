using System.Text.Json;
using DataService.API.Database;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Jobs;

public sealed class MarketWatchBootstrapService : BackgroundService
{
    private const string MarketWatchParamsHash = "market_watch:singleton:v1";
    private const string MarketWatchTargetTable = "market_watch_live";

    private readonly DatasetJobsRepository _jobs;
    private readonly IOptions<DataServiceSettings> _options;
    private readonly ILogger<MarketWatchBootstrapService> _log;

    public MarketWatchBootstrapService(
        DatasetJobsRepository jobs,
        IOptions<DataServiceSettings> options,
        ILogger<MarketWatchBootstrapService> log)
    {
        _jobs = jobs;
        _options = options;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var settings = _options.Value.MarketWatch;
                if (!settings.Enabled)
                {
                    await Task.Delay(TimeSpan.FromSeconds(Math.Max(5, settings.BootstrapIntervalSeconds)), stoppingToken);
                    continue;
                }

                if (!_jobs.SchemaReady)
                {
                    await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
                    continue;
                }

                var paramsJson = JsonSerializer.Serialize(new
                {
                    mode = "singleton_live_market_watch",
                    exchanges = settings.Exchanges,
                    timeframes = settings.Timeframes,
                    flush_interval_ms = settings.FlushIntervalMs,
                });

                var (job, deduped) = await _jobs.StartAsync(new DatasetJobStartRequest(
                    Type: DatasetJobType.MarketWatch,
                    TargetTable: MarketWatchTargetTable,
                    TargetSymbol: null,
                    TargetTimeframe: null,
                    TargetStartMs: null,
                    TargetEndMs: null,
                    ParamsJson: paramsJson,
                    ParamsHash: MarketWatchParamsHash,
                    CreatedBy: "system.market_watch.bootstrap"), stoppingToken);

                if (!deduped)
                {
                    _log.LogInformation("Queued singleton market watch job {JobId}", job.JobId);
                }
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Failed to ensure singleton market watch job");
            }

            var delaySeconds = Math.Max(5, _options.Value.MarketWatch.BootstrapIntervalSeconds);
            await Task.Delay(TimeSpan.FromSeconds(delaySeconds), stoppingToken);
        }
    }
}