using DataService.API.Database;
using DataService.API.Kafka;

namespace DataService.API.Jobs;

/// <summary>
/// Phase B scheduler: polls dataset_jobs for queued rows, dispatches them
/// to <see cref="IDatasetJobHandler"/> implementations, enforces per-type
/// capacity slots and (table, conflict_class) locks, and reclaims orphan
/// rows from previous runs on startup.
/// </summary>
public sealed class DatasetJobRunner : BackgroundService
{
    // Per-type concurrency caps. Tuned to keep the Postgres pool (size 100)
    // and Bybit rate-limit budget reasonable. Single-instance deployment
    // — these are process-local semaphores.
    private static readonly Dictionary<string, int> Caps = new(StringComparer.OrdinalIgnoreCase)
    {
        [DatasetJobType.Ingest]          = 4,
        [DatasetJobType.DetectAnomalies] = 8,
        [DatasetJobType.ComputeFeatures] = 2,
        [DatasetJobType.CleanApply]      = 2,
        [DatasetJobType.Export]          = 2,
        [DatasetJobType.ImportCsv]       = 2,
        [DatasetJobType.UpsertOhlcv]     = 4,
    };

    private readonly Dictionary<string, SemaphoreSlim> _slots;
    private readonly Dictionary<string, IDatasetJobHandler> _handlers;
    private readonly DatasetJobsRepository _repo;
    private readonly DatasetJobsMutator _mut;
    private readonly JobLockManager _locks;
    private readonly KafkaProducer _producer;
    private readonly ILogger<DatasetJobRunner> _log;

    public DatasetJobRunner(
        IEnumerable<IDatasetJobHandler> handlers,
        DatasetJobsRepository repo,
        DatasetJobsMutator mut,
        JobLockManager locks,
        KafkaProducer producer,
        ILogger<DatasetJobRunner> log)
    {
        _repo = repo;
        _mut = mut;
        _locks = locks;
        _producer = producer;
        _log = log;
        _handlers = handlers.ToDictionary(h => h.Type, StringComparer.OrdinalIgnoreCase);
        _slots = Caps.ToDictionary(kv => kv.Key, kv => new SemaphoreSlim(kv.Value, kv.Value), StringComparer.OrdinalIgnoreCase);
    }

    protected override async Task ExecuteAsync(CancellationToken stopping)
    {
        try
        {
            var orphans = await _mut.ReclaimOrphansAsync(stopping);
            if (orphans > 0) _log.LogWarning("DatasetJobRunner reclaimed {N} orphan running jobs", orphans);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to reclaim orphan jobs on startup");
        }

        _log.LogInformation("DatasetJobRunner started, handlers: {Types}",
            string.Join(", ", _handlers.Keys));

        while (!stopping.IsCancellationRequested)
        {
            try
            {
                var queued = await _repo.ListAsync("active", null, null, 50, stopping);
                var picked = 0;
                foreach (var job in queued.Where(j => j.Status == DatasetJobStatus.Queued))
                {
                    if (!_handlers.TryGetValue(job.Type, out var handler))
                    {
                        _log.LogWarning("No handler for job type {Type}; failing {JobId}", job.Type, job.JobId);
                        await _mut.FinishAsync(job.JobId, DatasetJobStatus.Failed,
                            "no_handler", $"no handler registered for type {job.Type}", stopping);
                        continue;
                    }

                    if (!_slots.TryGetValue(job.Type, out var slot)) continue;
                    if (slot.CurrentCount == 0) continue;

                    if (!_locks.TryAcquire(job.TargetTable, job.ConflictClass)) continue;

                    if (!await _mut.TryAcquireRunningAsync(job.JobId, stopping))
                    {
                        // Lost the race — someone else moved it; release our lock.
                        _locks.Release(job.TargetTable, job.ConflictClass);
                        continue;
                    }

                    await slot.WaitAsync(stopping);
                    picked++;
                    _ = Task.Run(() => RunOneAsync(handler, job, slot, stopping), stopping);
                }

                await Task.Delay(picked > 0 ? 100 : 500, stopping);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogError(ex, "DatasetJobRunner loop error");
                try { await Task.Delay(2000, stopping); } catch { break; }
            }
        }
    }

    private async Task RunOneAsync(IDatasetJobHandler handler, DatasetJobRecord job, SemaphoreSlim slot, CancellationToken stopping)
    {
        var heartbeatCts = CancellationTokenSource.CreateLinkedTokenSource(stopping);
        var heartbeat = Task.Run(() => HeartbeatLoopAsync(job.JobId, heartbeatCts.Token));

        var ctx = new JobContext(job, stopping, _mut, _repo, _producer);
        try
        {
            await ctx.ReportAsync(stage: "starting", progress: 0, detail: $"running {job.Type}");
            await handler.ExecuteAsync(ctx);

            var cancelRequested = await _mut.IsCancelRequestedAsync(job.JobId, stopping);
            var status = cancelRequested ? DatasetJobStatus.Canceled : DatasetJobStatus.Succeeded;
            await _mut.FinishAsync(job.JobId, status, ct: stopping);
            await PublishCompleted(job, status, null, null, stopping);
        }
        catch (OperationCanceledException)
        {
            await _mut.FinishAsync(job.JobId, DatasetJobStatus.Canceled, "canceled", "Job canceled", stopping);
            await PublishCompleted(job, DatasetJobStatus.Canceled, "canceled", "Job canceled", stopping);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Job {JobId} ({Type}) failed", job.JobId, job.Type);
            await _mut.FinishAsync(job.JobId, DatasetJobStatus.Failed, ex.GetType().Name, ex.Message, stopping);
            await PublishCompleted(job, DatasetJobStatus.Failed, ex.GetType().Name, ex.Message, stopping);
        }
        finally
        {
            heartbeatCts.Cancel();
            try { await heartbeat; } catch { /* heartbeat exit is best-effort */ }
            _locks.Release(job.TargetTable, job.ConflictClass);
            slot.Release();
        }
    }

    private async Task HeartbeatLoopAsync(Guid jobId, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try { await _mut.HeartbeatAsync(jobId, ct); } catch { /* ignore */ }
            try { await Task.Delay(5000, ct); } catch { return; }
        }
    }

    private Task PublishCompleted(DatasetJobRecord job, string status, string? errorCode, string? errorMessage, CancellationToken ct) =>
        _producer.PublishEventAsync(Topics.EvtDataDatasetJobCompleted, new
        {
            job_id        = job.JobId,
            type          = job.Type,
            status,
            error_code    = errorCode,
            error_message = errorMessage,
            ts            = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        }, ct);
}
