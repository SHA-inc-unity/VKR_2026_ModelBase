using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Kafka;
using System.Collections.Concurrent;
using System.Text.Json;
using System.Threading.Channels;

namespace DataService.API.Jobs;

/// <summary>
/// Phase B scheduler — now push-driven via <see cref="JobDispatchChannel"/>.
///
/// Hot path:
///   KafkaConsumer.HandleJobsStartAsync inserts a row in <c>dataset_jobs</c>
///   and immediately calls <see cref="JobDispatchChannel.Publish"/>. The
///   runner's read loop wakes up under a millisecond, runs slot/lock/dedup
///   checks, and dispatches the job to its <see cref="IDatasetJobHandler"/>.
///
/// Safety net:
///   We still run a slow (2 s) DB poll as a backstop for jobs that were
///   queued during process restart, by external writers, or while the
///   in-memory channel was momentarily backed up because all per-type
///   semaphores were saturated and we therefore could not dispatch a
///   freshly-published hint.
///
/// What did NOT change:
///   • per-type / per-exchange / heavy-timeframe semaphores
///   • (table, conflict_class) JobLockManager locks
///   • dedup via the partial unique index on params_hash
///   • orphan reclaim on startup
/// </summary>
public sealed class DatasetJobRunner : BackgroundService
{
    private static readonly string[] IngestExchanges = ["bybit", "binance"];
    private const int IngestSlotsPerExchange = 4;
    // Heavy timeframes (1m/3m) used to be limited to ONE concurrent ingest per
    // exchange. With the chart-ingest fast-path (skip_features) the per-job
    // wall-clock dropped from 5-10s to <1s, so we can safely admit more
    // parallel heavy-tf requests without exhausting the per-exchange API
    // budget. Three lets up to three users open a 1m chart on the same
    // exchange simultaneously instead of serializing them.
    private const int HeavyIngestSlotsPerExchange = 3;

    // Per-type concurrency caps. Tuned to keep the Postgres pool (size 100)
    // and Bybit rate-limit budget reasonable. Single-instance deployment
    // — these are process-local semaphores.
    private static readonly Dictionary<string, int> Caps = new(StringComparer.OrdinalIgnoreCase)
    {
        [DatasetJobType.Ingest]          = IngestSlotsPerExchange * IngestExchanges.Length,
        [DatasetJobType.DetectAnomalies] = 8,
        [DatasetJobType.ComputeFeatures] = 2,
        [DatasetJobType.CleanApply]      = 2,
        [DatasetJobType.Export]          = 2,
        [DatasetJobType.ImportCsv]       = 2,
        [DatasetJobType.UpsertOhlcv]     = 4,
    };

    // Extra gate for heavy timeframes (1m, 3m): at most 1 may run concurrently
    // per exchange, so Binance cannot block Bybit heavy jobs and vice versa.
    private readonly Dictionary<string, SemaphoreSlim> _heavyIngestSlotsByExchange;
    private readonly Dictionary<string, SemaphoreSlim> _ingestSlotsByExchange;

    private readonly Dictionary<string, SemaphoreSlim> _slots;
    private readonly Dictionary<string, IDatasetJobHandler> _handlers;
    private readonly DatasetJobsRepository _repo;
    private readonly DatasetJobsMutator _mut;
    private readonly JobLockManager _locks;
    private readonly KafkaProducer _producer;
    private readonly JobDispatchChannel _dispatch;
    private readonly JobCompletionTracker _completion;
    private readonly ILogger<DatasetJobRunner> _log;

    // Jobs we couldn't dispatch on first pull (slot saturated, lock taken,
    // etc.) are tracked here so the safety-net poll knows whether to re-fetch
    // them or whether a different scheduler instance already claimed them.
    private readonly ConcurrentDictionary<Guid, byte> _inflight = new();

    public DatasetJobRunner(
        IEnumerable<IDatasetJobHandler> handlers,
        DatasetJobsRepository repo,
        DatasetJobsMutator mut,
        JobLockManager locks,
        KafkaProducer producer,
        JobDispatchChannel dispatch,
        JobCompletionTracker completion,
        ILogger<DatasetJobRunner> log)
    {
        _repo = repo;
        _mut = mut;
        _locks = locks;
        _producer = producer;
        _dispatch = dispatch;
        _completion = completion;
        _log = log;
        _handlers = handlers.ToDictionary(h => h.Type, StringComparer.OrdinalIgnoreCase);
        _slots = Caps.ToDictionary(kv => kv.Key, kv => new SemaphoreSlim(kv.Value, kv.Value), StringComparer.OrdinalIgnoreCase);
        _ingestSlotsByExchange = BuildExchangeSlots(IngestSlotsPerExchange);
        _heavyIngestSlotsByExchange = BuildExchangeSlots(HeavyIngestSlotsPerExchange);
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

            var legacyMarketWatch = await _mut.DeleteLegacyMarketWatchAsync(stopping);
            if (legacyMarketWatch > 0)
                _log.LogInformation("DatasetJobRunner removed {N} legacy market_watch queue rows", legacyMarketWatch);

            // Backfill: anything that was already queued before this process
            // started (or queued by an external writer) won't have a channel
            // hint waiting. Seed the dispatch channel with them now.
            var queuedAtStartup = await _mut.PickQueuedAsync(100, stopping);
            foreach (var job in queuedAtStartup)
                _dispatch.Publish(job);
            if (queuedAtStartup.Count > 0)
                _log.LogInformation(
                    "DatasetJobRunner seeded dispatch channel with {N} pre-existing queued jobs",
                    queuedAtStartup.Count);
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to reclaim orphan jobs on startup");
        }

        _log.LogInformation(
            "DatasetJobRunner started (push-driven via JobDispatchChannel), handlers: {Types}",
            string.Join(", ", _handlers.Keys));

        // Run the safety-net poll as a separate background task. The main
        // loop is purely reactive — it parks on channel.Reader.ReadAsync.
        _ = Task.Run(() => SafetyNetPollLoopAsync(stopping), stopping);

        try
        {
            await foreach (var job in _dispatch.Reader.ReadAllAsync(stopping))
            {
                try { await TryDispatchAsync(job, stopping); }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    _log.LogError(ex, "DatasetJobRunner dispatch loop error for job {JobId}", job.JobId);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // graceful shutdown
        }
    }

    // Previously this scan ran every 2 s, which meant that a hint dropped
    // because of a saturated slot could wait up to 2 s before being retried
    // even though the slot freed almost immediately. We now poll at 250 ms —
    // the SELECT (PickQueuedAsync) is cheap and the latency win is significant
    // for chart-ingest where the per-job wall-clock is under a second in the
    // skip_features fast-path.
    private const int SafetyNetPollIntervalMs = 250;

    /// <summary>
    /// Backstop DB scan for missed channel signals — e.g. jobs queued while
    /// every per-type semaphore was saturated and we had to drop the hint,
    /// or jobs created by an external process that bypassed the channel
    /// publisher. Republishes any queued rows we don't already know about
    /// into the dispatch channel.
    /// </summary>
    private async Task SafetyNetPollLoopAsync(CancellationToken stopping)
    {
        while (!stopping.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(SafetyNetPollIntervalMs, stopping);
                var queued = await _mut.PickQueuedAsync(50, stopping);
                foreach (var job in queued)
                {
                    if (_inflight.ContainsKey(job.JobId)) continue;
                    _dispatch.Publish(job);
                }
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogError(ex, "DatasetJobRunner safety-net poll error");
                try { await Task.Delay(SafetyNetPollIntervalMs, stopping); } catch { break; }
            }
        }
    }

    private async Task TryDispatchAsync(DatasetJobRecord hint, CancellationToken stopping)
    {
        if (!_handlers.TryGetValue(hint.Type, out var handler))
        {
            _log.LogWarning("No handler for job type {Type}; failing {JobId}", hint.Type, hint.JobId);
            await _mut.FinishAsync(hint.JobId, DatasetJobStatus.Failed,
                "no_handler", $"no handler registered for type {hint.Type}", stopping);
            return;
        }

        if (!_slots.TryGetValue(hint.Type, out var slot)) return;

        // Soft non-blocking check first — if there's no free slot right now,
        // drop the hint and let the safety-net poll re-pick it once a slot
        // frees. This keeps the hot path lock-free.
        if (slot.CurrentCount == 0) return;

        SemaphoreSlim? exchangeSlot = null;
        SemaphoreSlim? heavySlot = null;
        var ingestExchange = ResolveIngestExchange(hint);

        if (string.Equals(hint.Type, DatasetJobType.Ingest, StringComparison.OrdinalIgnoreCase))
        {
            exchangeSlot = GetExchangeSlot(_ingestSlotsByExchange, ingestExchange);
            if (exchangeSlot.CurrentCount == 0) return;
        }

        var isHeavy = IsHeavyIngest(hint);
        if (isHeavy)
        {
            heavySlot = GetExchangeSlot(_heavyIngestSlotsByExchange, ingestExchange);
            if (heavySlot.CurrentCount == 0) return;
        }

        if (!_locks.TryAcquire(hint.TargetTable, hint.ConflictClass)) return;

        // Atomic queued→running transition. If we lose the race (another
        // poll picked it up, or the job was canceled), release the lock and
        // bail.
        if (!await _mut.TryAcquireRunningAsync(hint.JobId, stopping))
        {
            _locks.Release(hint.TargetTable, hint.ConflictClass);
            return;
        }

        // Slot acquisitions cannot block here — we already verified
        // CurrentCount > 0 above, and ours is the only thread that
        // transitions queued→running for this row. Use Wait(0) to assert.
        await slot.WaitAsync(stopping);
        if (exchangeSlot is not null) await exchangeSlot.WaitAsync(stopping);
        if (heavySlot is not null) await heavySlot.WaitAsync(stopping);

        _inflight[hint.JobId] = 0;
        _ = Task.Run(async () =>
        {
            try { await RunOneAsync(handler, hint, slot, exchangeSlot, heavySlot, stopping); }
            finally { _inflight.TryRemove(hint.JobId, out _); }
        }, stopping);
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
            // Wake up any cmd.data.dataset.jobs.get waiter that is server-side
            // long-polling for this job. The DB row is already in a terminal
            // state by this point (FinishAsync ran in the try/catch above),
            // so the waiter will read a consistent record.
            _completion.Signal(job.JobId);
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
        IngestExchanges.ToDictionary(
            exchange => exchange,
            _ => new SemaphoreSlim(cap, cap),
            StringComparer.OrdinalIgnoreCase);

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
            started_at        = job.StartedAt?.ToUniversalTime().ToString("O"),
            finished_at       = job.FinishedAt?.ToUniversalTime().ToString("O"),
            ts                = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        }, ct);
}
