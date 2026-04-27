using Dapper;
using Npgsql;

namespace DataService.API.Database;

/// <summary>
/// Mutating SQL operations on dataset_jobs / subtasks / stages used by the
/// DatasetJobRunner (Phase B). Kept separate from the read-only Phase-A repo
/// surface so that handler code does not casually mutate state.
/// </summary>
public sealed class DatasetJobsMutator
{
    private readonly PostgresConnectionFactory _pg;
    public DatasetJobsMutator(PostgresConnectionFactory pg) { _pg = pg; }

    // ── Lifecycle: queued → running → terminal ───────────────────────────

    public async Task<bool> TryAcquireRunningAsync(Guid jobId, CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_jobs
               SET status='running', started_at=now(), updated_at=now()
             WHERE job_id=@JobId AND status='queued'
            """;
        await using var conn = await _pg.OpenAsync(ct);
        var n = await conn.ExecuteAsync(new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
        return n > 0;
    }

    public async Task UpdateProgressAsync(
        Guid jobId, string? stage, short progress, string? detail,
        long? total = null, long? completed = null, long? failed = null, long? skipped = null,
        CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_jobs SET
                stage     = COALESCE(@Stage, stage),
                progress  = @Progress,
                detail    = @Detail,
                total     = COALESCE(@Total,     total),
                completed = COALESCE(@Completed, completed),
                failed    = COALESCE(@Failed,    failed),
                skipped   = COALESCE(@Skipped,   skipped),
                updated_at= now()
            WHERE job_id=@JobId
            """;
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new {
            JobId = jobId, Stage = stage, Progress = progress, Detail = detail,
            Total = total, Completed = completed, Failed = failed, Skipped = skipped,
        }, cancellationToken: ct));
    }

    public async Task HeartbeatAsync(Guid jobId, CancellationToken ct = default)
    {
        const string sql = "UPDATE dataset_jobs SET updated_at=now() WHERE job_id=@JobId AND status='running'";
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
    }

    public async Task FinishAsync(
        Guid jobId, string status, string? errorCode = null, string? errorMessage = null,
        CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_jobs SET
                status=@Status, finished_at=now(), updated_at=now(),
                progress=CASE WHEN @Status='succeeded' THEN 100 ELSE progress END,
                error_code=@ErrorCode, error_message=@ErrorMessage
            WHERE job_id=@JobId
            """;
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new {
            JobId = jobId, Status = status, ErrorCode = errorCode, ErrorMessage = errorMessage,
        }, cancellationToken: ct));
    }

    // ── Queue picking ────────────────────────────────────────────────────

    /// <summary>Select up to <paramref name="limit"/> queued jobs ordered by created_at.</summary>
    public async Task<IReadOnlyList<DatasetJobRecord>> PickQueuedAsync(int limit, CancellationToken ct = default)
    {
        var sql = $"SELECT * FROM dataset_jobs WHERE status='queued' ORDER BY created_at LIMIT {Math.Clamp(limit, 1, 100)}";
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<dynamic>(new CommandDefinition(sql, cancellationToken: ct));
        return rows.Select(r => DatasetJobsRepository.MapJobPublic((object)r)).ToList();
    }

    public async Task<bool> IsCancelRequestedAsync(Guid jobId, CancellationToken ct = default)
    {
        const string sql = "SELECT cancel_requested FROM dataset_jobs WHERE job_id=@JobId";
        await using var conn = await _pg.OpenAsync(ct);
        return await conn.ExecuteScalarAsync<bool>(new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
    }

    // ── Recovery ─────────────────────────────────────────────────────────

    /// <summary>
    /// Mark every still-running job as failed (service_restart). Called on
    /// service startup before the scheduler begins picking up queued work.
    /// </summary>
    public async Task<int> ReclaimOrphansAsync(CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_jobs SET
                status='failed', finished_at=now(), updated_at=now(),
                error_code='service_restart',
                error_message='Service restarted while job was running; please retry.'
            WHERE status='running'
            """;
        await using var conn = await _pg.OpenAsync(ct);
        return await conn.ExecuteAsync(new CommandDefinition(sql, cancellationToken: ct));
    }

    // ── Subtasks ─────────────────────────────────────────────────────────

    public async Task<Guid> AddSubtaskAsync(
        Guid jobId, int idx, string label, string? targetTable, long total,
        CancellationToken ct = default)
    {
        var subtaskId = Guid.NewGuid();
        const string sql = """
            INSERT INTO dataset_job_subtasks (subtask_id, job_id, idx, label, target_table, status, total)
            VALUES (@SubtaskId, @JobId, @Idx, @Label, @TargetTable, 'queued', @Total)
            """;
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new {
            SubtaskId = subtaskId, JobId = jobId, Idx = idx, Label = label,
            TargetTable = targetTable, Total = total,
        }, cancellationToken: ct));
        return subtaskId;
    }

    public async Task UpdateSubtaskAsync(
        Guid subtaskId, string status, short progress, string? detail = null,
        long? completed = null, long? failed = null, long? skipped = null,
        string? errorCode = null, string? errorMessage = null,
        bool setStarted = false, bool setFinished = false,
        CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_job_subtasks SET
                status        = @Status,
                progress      = @Progress,
                detail        = COALESCE(@Detail,        detail),
                completed     = COALESCE(@Completed,     completed),
                failed        = COALESCE(@Failed,        failed),
                skipped       = COALESCE(@Skipped,       skipped),
                error_code    = COALESCE(@ErrorCode,     error_code),
                error_message = COALESCE(@ErrorMessage,  error_message),
                started_at    = CASE WHEN @SetStarted  THEN COALESCE(started_at, now()) ELSE started_at END,
                finished_at   = CASE WHEN @SetFinished THEN now() ELSE finished_at END
            WHERE subtask_id=@SubtaskId
            """;
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new {
            SubtaskId = subtaskId, Status = status, Progress = progress, Detail = detail,
            Completed = completed, Failed = failed, Skipped = skipped,
            ErrorCode = errorCode, ErrorMessage = errorMessage,
            SetStarted = setStarted, SetFinished = setFinished,
        }, cancellationToken: ct));
    }

    // ── Stages (observability) ───────────────────────────────────────────

    public async Task<Guid> StartStageAsync(Guid jobId, string name, Guid? subtaskId = null, CancellationToken ct = default)
    {
        var id = Guid.NewGuid();
        const string sql = """
            INSERT INTO dataset_job_stages (stage_id, job_id, subtask_id, name)
            VALUES (@StageId, @JobId, @SubtaskId, @Name)
            """;
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new {
            StageId = id, JobId = jobId, SubtaskId = subtaskId, Name = name,
        }, cancellationToken: ct));
        return id;
    }

    public async Task EndStageAsync(Guid stageId, long? itemsProcessed = null, string? customMetricsJson = null, CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_job_stages SET
                ended_at        = now(),
                items_processed = @Items,
                custom_metrics  = COALESCE(@Metrics::jsonb, custom_metrics)
            WHERE stage_id=@StageId
            """;
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, new {
            StageId = stageId, Items = itemsProcessed, Metrics = customMetricsJson,
        }, cancellationToken: ct));
    }
}
