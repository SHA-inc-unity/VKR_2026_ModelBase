using DataService.API.Database;
using DataService.API.Kafka;

namespace DataService.API.Jobs;

/// <summary>
/// Live job state passed to handlers. Lets handler code report progress
/// without having to know about Kafka topics or DB column names.
/// </summary>
public sealed class JobContext
{
    public DatasetJobRecord Job { get; }
    public CancellationToken CancellationToken { get; }
    private readonly DatasetJobsMutator _mut;
    private readonly DatasetJobsRepository _repo;
    private readonly KafkaProducer _producer;
    private string? _currentStage;
    private short _currentProgress;
    private string? _currentDetail;
    private short? _currentStageProgress;
    private long? _currentStageTotal;
    private long? _currentStageCompleted;
    private long? _currentStageFailed;
    private long? _currentStageSkipped;
    private long _currentTotal;
    private long _currentCompleted;
    private long _currentFailed;
    private long _currentSkipped;

    public JobContext(
        DatasetJobRecord job,
        CancellationToken ct,
        DatasetJobsMutator mut,
        DatasetJobsRepository repo,
        KafkaProducer producer)
    {
        Job = job;
        CancellationToken = ct;
        _mut = mut;
        _repo = repo;
        _producer = producer;
        _currentStage = job.Stage;
        _currentProgress = job.Progress;
        _currentDetail = job.Detail;
        _currentStageProgress = job.StageProgress;
        _currentStageTotal = job.StageTotal;
        _currentStageCompleted = job.StageCompleted;
        _currentStageFailed = job.StageFailed;
        _currentStageSkipped = job.StageSkipped;
        _currentTotal = job.Total;
        _currentCompleted = job.Completed;
        _currentFailed = job.Failed;
        _currentSkipped = job.Skipped;
    }

    public Guid JobId => Job.JobId;

    public async Task ReportAsync(
        string? stage = null, int? progress = null, string? detail = null,
        int? stageProgress = null,
        long? stageTotal = null, long? stageCompleted = null, long? stageFailed = null, long? stageSkipped = null,
        long? total = null, long? completed = null, long? failed = null, long? skipped = null,
        CancellationToken? ct = null)
    {
        var token = ct ?? CancellationToken;
        var nextStage = stage ?? _currentStage;
        var stageChanged = stage is not null && !string.Equals(stage, _currentStage, StringComparison.Ordinal);
        var nextProgress = (short)Math.Clamp(progress ?? _currentProgress, 0, 100);
        var nextDetail = detail ?? (stageChanged ? null : _currentDetail);
        var nextStageProgress = stageChanged
            ? (stageProgress is null ? (short?)null : (short)Math.Clamp(stageProgress.Value, 0, 100))
            : (stageProgress is null ? _currentStageProgress : (short)Math.Clamp(stageProgress.Value, 0, 100));
        var nextStageTotal = stageChanged ? stageTotal : stageTotal ?? _currentStageTotal;
        var nextStageCompleted = stageChanged ? stageCompleted : stageCompleted ?? _currentStageCompleted;
        var nextStageFailed = stageChanged ? stageFailed : stageFailed ?? _currentStageFailed;
        var nextStageSkipped = stageChanged ? stageSkipped : stageSkipped ?? _currentStageSkipped;
        var nextTotal = total ?? _currentTotal;
        var nextCompleted = completed ?? _currentCompleted;
        var nextFailed = failed ?? _currentFailed;
        var nextSkipped = skipped ?? _currentSkipped;

        await _mut.UpdateProgressAsync(
            Job.JobId, nextStage, nextProgress, nextDetail,
            nextStageProgress, nextStageTotal, nextStageCompleted, nextStageFailed, nextStageSkipped,
            nextTotal, nextCompleted, nextFailed, nextSkipped, token);
        await _producer.PublishEventAsync(Topics.EvtDataDatasetJobProgress, new
        {
            job_id            = Job.JobId,
            type              = Job.Type,
            status            = "running",       // always running while a handler reports
            target_table      = Job.TargetTable,
            stage             = nextStage,
            progress          = nextProgress,
            overall_progress  = nextProgress,
            stage_progress    = nextStageProgress,
            detail            = nextDetail,
            stage_total       = nextStageTotal,
            stage_completed   = nextStageCompleted,
            stage_failed      = nextStageFailed,
            stage_skipped     = nextStageSkipped,
            total             = nextTotal,
            completed         = nextCompleted,
            failed            = nextFailed,
            skipped           = nextSkipped,
            ts                = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        }, token);

        _currentStage = nextStage;
        _currentProgress = nextProgress;
        _currentDetail = nextDetail;
        _currentStageProgress = nextStageProgress;
        _currentStageTotal = nextStageTotal;
        _currentStageCompleted = nextStageCompleted;
        _currentStageFailed = nextStageFailed;
        _currentStageSkipped = nextStageSkipped;
        _currentTotal = nextTotal;
        _currentCompleted = nextCompleted;
        _currentFailed = nextFailed;
        _currentSkipped = nextSkipped;
    }

    public async Task<bool> IsCancelRequestedAsync()
    {
        if (CancellationToken.IsCancellationRequested) return true;
        return await _mut.IsCancelRequestedAsync(Job.JobId, CancellationToken);
    }

    /// <summary>
    /// Runs a long operation with a linked cancellation token that is tripped
    /// as soon as the job-level cancel flag appears in the database.
    /// </summary>
    public async Task<T> RunCancelableAsync<T>(
        Func<CancellationToken, Task<T>> operation,
        TimeSpan? pollInterval = null)
    {
        if (await IsCancelRequestedAsync())
        {
            throw new OperationCanceledException(CancellationToken);
        }

        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(CancellationToken);
        // 2 s default — was 500 ms. The cancel button has a "few seconds"
        // budget anyway, and the previous interval hammered the DB pool with
        // a cheap-but-frequent SELECT on every running ingest job. Bigger
        // jobs that benefit from snappier cancellation can still override
        // it explicitly via the parameter.
        var monitorTask = MonitorCancelRequestedAsync(
            linkedCts,
            pollInterval ?? TimeSpan.FromSeconds(2));

        try
        {
            return await operation(linkedCts.Token);
        }
        finally
        {
            if (!linkedCts.IsCancellationRequested)
            {
                linkedCts.Cancel();
            }

            try { await monitorTask; }
            catch (OperationCanceledException) { }
        }
    }

    private async Task MonitorCancelRequestedAsync(CancellationTokenSource linkedCts, TimeSpan interval)
    {
        try
        {
            while (!linkedCts.IsCancellationRequested)
            {
                if (await IsCancelRequestedAsync())
                {
                    linkedCts.Cancel();
                    break;
                }

                await Task.Delay(interval, linkedCts.Token);
            }
        }
        catch (OperationCanceledException) when (linkedCts.IsCancellationRequested || CancellationToken.IsCancellationRequested)
        {
        }
    }

    public Task<Guid> AddSubtaskAsync(int idx, string label, string? targetTable, long total) =>
        _mut.AddSubtaskAsync(Job.JobId, idx, label, targetTable, total, CancellationToken);

    public Task UpdateSubtaskAsync(
        Guid subtaskId, string status, int progress, string? detail = null,
        long? completed = null, long? failed = null, long? skipped = null,
        string? errorCode = null, string? errorMessage = null,
        bool setStarted = false, bool setFinished = false) =>
        _mut.UpdateSubtaskAsync(subtaskId, status, (short)Math.Clamp(progress, 0, 100), detail,
            completed, failed, skipped, errorCode, errorMessage, setStarted, setFinished, CancellationToken);

    public Task<Guid> StartStageAsync(string name, Guid? subtaskId = null) =>
        _mut.StartStageAsync(Job.JobId, name, subtaskId, CancellationToken);

    public Task EndStageAsync(Guid stageId, long? items = null, string? metricsJson = null) =>
        _mut.EndStageAsync(stageId, items, metricsJson, CancellationToken);
}

/// <summary>
/// Strategy contract for executing one job type. Implementations may throw
/// <see cref="OperationCanceledException"/> to signal cancellation; any
/// other exception marks the job as failed with the exception message.
/// </summary>
public interface IDatasetJobHandler
{
    string Type { get; }
    Task ExecuteAsync(JobContext ctx);
}
