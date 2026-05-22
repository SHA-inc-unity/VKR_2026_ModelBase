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
    }

    public Guid JobId => Job.JobId;

    public async Task ReportAsync(
        string? stage = null, int? progress = null, string? detail = null,
        long? total = null, long? completed = null, long? failed = null, long? skipped = null,
        CancellationToken? ct = null)
    {
        var token = ct ?? CancellationToken;
        await _mut.UpdateProgressAsync(
            Job.JobId, stage, (short)Math.Clamp(progress ?? Job.Progress, 0, 100), detail,
            total, completed, failed, skipped, token);
        await _producer.PublishEventAsync(Topics.EvtDataDatasetJobProgress, new
        {
            job_id       = Job.JobId,
            type         = Job.Type,
            status       = "running",       // always running while a handler reports
            target_table = Job.TargetTable,
            stage,
            progress     = progress ?? Job.Progress,
            detail,
            total, completed, failed, skipped,
            ts           = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        }, token);
    }

    public async Task<bool> IsCancelRequestedAsync()
    {
        if (CancellationToken.IsCancellationRequested) return true;
        return await _mut.IsCancelRequestedAsync(Job.JobId, CancellationToken);
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
