using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Kafka;
using System.Text.Json;

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
    //
    // Ingest cap is isolated per exchange: one exchange cannot starve the others.
    // Each exchange gets 4 slots; non-ingest job types keep the global per-type caps.
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

    // Extra gate for heavy timeframes (1m, 3m): at most 1 may run concurrently
    // per exchange, so Kraken cannot block Bybit heavy jobs and vice versa.
    private readonly Dictionary<string, SemaphoreSlim> _heavyIngestSlotsByExchange;
    private readonly Dictionary<string, SemaphoreSlim> _ingestSlotsByExchange;

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
        _ingestSlotsByExchange = BuildExchangeSlots(4);
        _heavyIngestSlotsByExchange = BuildExchangeSlots(1);
    }

    protected override async Task ExecuteAsync(CancellationToken stopping)
    {
        await WaitForSchemaReadyAsync(stopping);

        try
        {
            var invalidQueued = await _mut.FailInvalidQueuedAsync(stopping);
            if (invalidQueued > 0)
                _log.LogWarning("DatasetJobRunner soft-failed {N} invalid queued jobs during startup recovery", invalidQueued);

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
                var queued = await _mut.PickQueuedAsync(50, stopping);
                var picked = 0;
                foreach (var job in queued)
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

                    SemaphoreSlim? exchangeSlot = null;
                    SemaphoreSlim? heavySlot = null;
                    var ingestExchange = ResolveIngestExchange(job);

                    if (string.Equals(job.Type, DatasetJobType.Ingest, StringComparison.OrdinalIgnoreCase))
                    {
                        exchangeSlot = GetExchangeSlot(_ingestSlotsByExchange, ingestExchange);
                        if (exchangeSlot.CurrentCount == 0) continue;
                    }

                    // Heavy timeframes (1m, 3m) additionally require the exclusive
                    // heavy-ingest slot so they cannot run two at a time.
                    var isHeavy = IsHeavyIngest(job);
                    if (isHeavy)
                    {
                        heavySlot = GetExchangeSlot(_heavyIngestSlotsByExchange, ingestExchange);
                        if (heavySlot.CurrentCount == 0) continue;
                    }

                    if (!_locks.TryAcquire(job.TargetTable, job.ConflictClass)) continue;

                    if (!await _mut.TryAcquireRunningAsync(job.JobId, stopping))
                    {
                        // Lost the race — someone else moved it; release our lock.
                        _locks.Release(job.TargetTable, job.ConflictClass);
                        continue;
                    }

                    await slot.WaitAsync(stopping);
                    if (exchangeSlot is not null) await exchangeSlot.WaitAsync(stopping);
                    if (heavySlot is not null) await heavySlot.WaitAsync(stopping);
                    picked++;
                    _ = Task.Run(() => RunOneAsync(handler, job, slot,
                        exchangeSlot, heavySlot, stopping), stopping);
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

    private async Task WaitForSchemaReadyAsync(CancellationToken ct)
    {
        if (_repo.SchemaReady) return;

        var attempt = 0;
        while (!ct.IsCancellationRequested && !_repo.SchemaReady)
        {
            try
            {
                await _repo.EnsureSchemaAsync(ct);
                return;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                attempt++;
                var delay = TimeSpan.FromSeconds(Math.Min(30, Math.Pow(2, Math.Min(attempt, 5))));
                _log.LogWarning(ex,
                    "DatasetJobRunner schema bootstrap failed (attempt {Attempt}); retrying in {Delay}s",
                    attempt, delay.TotalSeconds);
                await Task.Delay(delay, ct);
            }
        }
    }

    private async Task RunOneAsync(
        IDatasetJobHandler handler, DatasetJobRecord job,
        SemaphoreSlim slot, SemaphoreSlim? exchangeSlot, SemaphoreSlim? heavySlot, CancellationToken stopping)
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
            var finalJob = await TryReadFinalJobAsync(job, stopping);
            await PublishCompleted(finalJob, status, null, null, stopping);
        }
        catch (OperationCanceledException)
        {
            await _mut.FinishAsync(job.JobId, DatasetJobStatus.Canceled, "canceled", "Job canceled", stopping);
            var finalJob = await TryReadFinalJobAsync(job, stopping);
            await PublishCompleted(finalJob, DatasetJobStatus.Canceled, "canceled", "Job canceled", stopping);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Job {JobId} ({Type}) failed", job.JobId, job.Type);
            await _mut.FinishAsync(job.JobId, DatasetJobStatus.Failed, ex.GetType().Name, ex.Message, stopping);
            var finalJob = await TryReadFinalJobAsync(job, stopping);
            await PublishCompleted(finalJob, DatasetJobStatus.Failed, ex.GetType().Name, ex.Message, stopping);
        }
        finally
        {
            heartbeatCts.Cancel();
            try { await heartbeat; } catch { /* heartbeat exit is best-effort */ }
            _locks.Release(job.TargetTable, job.ConflictClass);
            heavySlot?.Release();
            exchangeSlot?.Release();
            slot.Release();
        }
    }

    /// <summary>Re-read the job record from the DB to get final counters
    /// (completed, failed, etc.) that were updated during execution.
    /// Falls back to the in-memory snapshot if the query fails.</summary>
    private async Task<DatasetJobRecord> TryReadFinalJobAsync(
        DatasetJobRecord fallback, CancellationToken ct)
    {
        try { return await _repo.GetByIdAsync(fallback.JobId, ct) ?? fallback; }
        catch { return fallback; }
    }

    private static bool IsHeavyIngest(DatasetJobRecord job) =>
        job.Type == DatasetJobType.Ingest &&
        job.TargetTimeframe is { } tf &&
        DatasetConstants.HeavyTimeframes.Contains(tf);

    private static Dictionary<string, SemaphoreSlim> BuildExchangeSlots(int cap) =>
        new(StringComparer.OrdinalIgnoreCase)
        {
            ["bybit"] = new SemaphoreSlim(cap, cap),
            ["binance"] = new SemaphoreSlim(cap, cap),
            ["kraken"] = new SemaphoreSlim(cap, cap),
        };

    private static SemaphoreSlim GetExchangeSlot(Dictionary<string, SemaphoreSlim> slots, string exchange) =>
        slots.TryGetValue(exchange, out var slot) ? slot : slots["bybit"];

    private static string ResolveIngestExchange(DatasetJobRecord job)
    {
        if (!string.Equals(job.Type, DatasetJobType.Ingest, StringComparison.OrdinalIgnoreCase))
            return "bybit";

        if (!string.IsNullOrWhiteSpace(job.ParamsJson))
        {
            try
            {
                using var doc = JsonDocument.Parse(job.ParamsJson);
                if (doc.RootElement.ValueKind == JsonValueKind.Object
                    && doc.RootElement.TryGetProperty("exchange", out var exchange)
                    && exchange.ValueKind == JsonValueKind.String)
                {
                    var value = exchange.GetString()?.Trim().ToLowerInvariant();
                    if (!string.IsNullOrWhiteSpace(value)) return value;
                }
            }
            catch
            {
                // Fall back to target_table inference below.
            }
        }

        if (job.TargetTable is { } table)
        {
            if (table.StartsWith("kraken_", StringComparison.OrdinalIgnoreCase)) return "kraken";
            if (table.StartsWith("binance_", StringComparison.OrdinalIgnoreCase)) return "binance";
        }

        return "bybit";
    }

    private async Task HeartbeatLoopAsync(Guid jobId, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try { await _mut.HeartbeatAsync(jobId, ct); } catch { /* ignore */ }
            try { await Task.Delay(5000, ct); } catch { return; }
        }
    }

    private Task PublishCompleted(
        DatasetJobRecord job, string status,
        string? errorCode, string? errorMessage, CancellationToken ct) =>
        _producer.PublishEventAsync(Topics.EvtDataDatasetJobCompleted, new
        {
            job_id            = job.JobId,
            type              = job.Type,
            status,
            target_table      = job.TargetTable,
            target_timeframe  = job.TargetTimeframe,
            stage             = job.Stage,
            progress          = job.Progress,
            overall_progress  = job.Progress,
            stage_progress    = job.StageProgress,
            detail            = job.Detail,
            stage_total       = job.StageTotal,
            stage_completed   = job.StageCompleted,
            stage_failed      = job.StageFailed,
            stage_skipped     = job.StageSkipped,
            total             = job.Total,
            completed         = job.Completed,
            failed            = job.Failed,
            skipped           = job.Skipped,
            error_code        = errorCode,
            error_message     = errorMessage,
            finished_at       = job.FinishedAt?.ToUniversalTime().ToString("O"),
            ts                = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        }, ct);
}
