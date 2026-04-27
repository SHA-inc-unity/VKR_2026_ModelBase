using System.Text.Json;
using Dapper;
using Npgsql;

namespace DataService.API.Database;

/// <summary>
/// CRUD layer for dataset background-job tracking tables (Phase A of the
/// jobs redesign).
///
/// This repository owns only the persistence schema. The
/// <c>DatasetJobRunner</c> background service (Phase B) is responsible for
/// actually executing jobs; until then, rows created via
/// <see cref="StartAsync"/> stay in <c>queued</c> state forever.
///
/// Schema is created idempotently in <see cref="EnsureSchemaAsync"/> on
/// service startup so the SQL migration script (<c>scripts/004_dataset_jobs.sql</c>)
/// is optional for ops folks.
/// </summary>
public sealed class DatasetJobsRepository
{
    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<DatasetJobsRepository> _log;

    public DatasetJobsRepository(PostgresConnectionFactory pg, ILogger<DatasetJobsRepository> log)
    {
        _pg  = pg;
        _log = log;
    }

    private const string CreateSchemaSql = """
        CREATE TABLE IF NOT EXISTS dataset_jobs (
            job_id            UUID         PRIMARY KEY,
            type              TEXT         NOT NULL,
            conflict_class    TEXT         NOT NULL,
            target_table      TEXT         NULL,
            target_symbol     TEXT         NULL,
            target_timeframe  TEXT         NULL,
            target_start_ms   BIGINT       NULL,
            target_end_ms     BIGINT       NULL,
            params_json       JSONB        NOT NULL DEFAULT '{}'::jsonb,
            params_hash       TEXT         NOT NULL,
            status            TEXT         NOT NULL,
            stage             TEXT         NULL,
            progress          SMALLINT     NOT NULL DEFAULT 0,
            detail            TEXT         NULL,
            total             BIGINT       NOT NULL DEFAULT 0,
            completed         BIGINT       NOT NULL DEFAULT 0,
            failed            BIGINT       NOT NULL DEFAULT 0,
            skipped           BIGINT       NOT NULL DEFAULT 0,
            error_code        TEXT         NULL,
            error_message     TEXT         NULL,
            cancel_requested  BOOLEAN      NOT NULL DEFAULT FALSE,
            created_by        TEXT         NULL,
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            started_at        TIMESTAMPTZ  NULL,
            finished_at       TIMESTAMPTZ  NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dataset_jobs_status_type
            ON dataset_jobs (status, type);
        CREATE INDEX IF NOT EXISTS idx_dataset_jobs_target_table
            ON dataset_jobs (target_table);
        CREATE INDEX IF NOT EXISTS idx_dataset_jobs_created_at
            ON dataset_jobs (created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dataset_jobs_active_params
            ON dataset_jobs (params_hash)
            WHERE status IN ('queued', 'running');

        CREATE TABLE IF NOT EXISTS dataset_job_subtasks (
            subtask_id      UUID         PRIMARY KEY,
            job_id          UUID         NOT NULL REFERENCES dataset_jobs(job_id) ON DELETE CASCADE,
            idx             INT          NOT NULL,
            label           TEXT         NOT NULL,
            target_table    TEXT         NULL,
            status          TEXT         NOT NULL,
            progress        SMALLINT     NOT NULL DEFAULT 0,
            detail          TEXT         NULL,
            total           BIGINT       NOT NULL DEFAULT 0,
            completed       BIGINT       NOT NULL DEFAULT 0,
            failed          BIGINT       NOT NULL DEFAULT 0,
            skipped         BIGINT       NOT NULL DEFAULT 0,
            error_code      TEXT         NULL,
            error_message   TEXT         NULL,
            started_at      TIMESTAMPTZ  NULL,
            finished_at     TIMESTAMPTZ  NULL,
            UNIQUE (job_id, idx)
        );
        CREATE INDEX IF NOT EXISTS idx_dataset_job_subtasks_job
            ON dataset_job_subtasks (job_id);

        CREATE TABLE IF NOT EXISTS dataset_job_stages (
            stage_id         UUID         PRIMARY KEY,
            job_id           UUID         NOT NULL REFERENCES dataset_jobs(job_id) ON DELETE CASCADE,
            subtask_id       UUID         NULL REFERENCES dataset_job_subtasks(subtask_id) ON DELETE CASCADE,
            name             TEXT         NOT NULL,
            started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            ended_at         TIMESTAMPTZ  NULL,
            items_processed  BIGINT       NULL,
            custom_metrics   JSONB        NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dataset_job_stages_job
            ON dataset_job_stages (job_id);
        """;

    /// <summary>
    /// Creates all jobs-tracking tables idempotently. Safe to call on every
    /// service startup.
    /// </summary>
    public async Task EnsureSchemaAsync(CancellationToken ct = default)
    {
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(CreateSchemaSql, cancellationToken: ct));
        _log.LogInformation("dataset_jobs schema ensured");
    }

    // ── Start / dedup ─────────────────────────────────────────────────────

    /// <summary>
    /// Insert a new job. If an active job (queued or running) with the same
    /// <see cref="DatasetJobStartRequest.ParamsHash"/> already exists, returns
    /// it instead (deduped=true). Both branches are race-safe thanks to the
    /// partial unique index on (params_hash) WHERE status IN ('queued','running').
    /// </summary>
    public async Task<(DatasetJobRecord Job, bool Deduped)> StartAsync(
        DatasetJobStartRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Type))
            throw new ArgumentException("Job type is required", nameof(req));
        if (!DatasetJobType.All.Contains(req.Type))
            throw new ArgumentException($"Unknown job type: {req.Type}", nameof(req));
        if (string.IsNullOrWhiteSpace(req.ParamsHash))
            throw new ArgumentException("params_hash is required", nameof(req));

        var conflictClass = DatasetJobType.ConflictClassOf(req.Type);
        var jobId = Guid.NewGuid();

        const string insertSql = """
            INSERT INTO dataset_jobs
                (job_id, type, conflict_class, target_table, target_symbol,
                 target_timeframe, target_start_ms, target_end_ms,
                 params_json, params_hash, status, progress, created_by)
            VALUES (@JobId, @Type, @ConflictClass, @TargetTable, @TargetSymbol,
                    @TargetTimeframe, @TargetStartMs, @TargetEndMs,
                    @ParamsJson::jsonb, @ParamsHash, 'queued', 0, @CreatedBy)
            ON CONFLICT ON CONSTRAINT uq_dataset_jobs_active_params DO NOTHING
            RETURNING job_id;
            """;

        await using var conn = await _pg.OpenAsync(ct);
        var insertedId = await conn.ExecuteScalarAsync<Guid?>(new CommandDefinition(insertSql, new
        {
            JobId           = jobId,
            req.Type,
            ConflictClass   = conflictClass,
            req.TargetTable,
            req.TargetSymbol,
            req.TargetTimeframe,
            req.TargetStartMs,
            req.TargetEndMs,
            ParamsJson      = string.IsNullOrEmpty(req.ParamsJson) ? "{}" : req.ParamsJson,
            req.ParamsHash,
            req.CreatedBy,
        }, cancellationToken: ct));

        if (insertedId is { } id)
        {
            var rec = await GetByIdAsync(id, ct)
                ?? throw new InvalidOperationException("Just-inserted job not found");
            return (rec, false);
        }

        // Conflict on the partial unique index → an active job with the same
        // params_hash already exists. Return that one with deduped=true.
        var existing = await FindActiveByParamsHashAsync(req.ParamsHash, ct)
            ?? throw new InvalidOperationException(
                "Dedup conflict but no active job found (race in transition).");
        return (existing, true);
    }

    public async Task<DatasetJobRecord?> GetByIdAsync(Guid jobId, CancellationToken ct = default)
    {
        const string sql = "SELECT * FROM dataset_jobs WHERE job_id = @JobId";
        await using var conn = await _pg.OpenAsync(ct);
        var row = await conn.QueryFirstOrDefaultAsync<dynamic>(
            new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
        return row is null ? null : MapJob(row);
    }

    public async Task<DatasetJobRecord?> FindActiveByParamsHashAsync(
        string paramsHash, CancellationToken ct = default)
    {
        const string sql = """
            SELECT * FROM dataset_jobs
            WHERE params_hash = @ParamsHash
              AND status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """;
        await using var conn = await _pg.OpenAsync(ct);
        var row = await conn.QueryFirstOrDefaultAsync<dynamic>(
            new CommandDefinition(sql, new { ParamsHash = paramsHash }, cancellationToken: ct));
        return row is null ? null : MapJob(row);
    }

    /// <summary>
    /// List jobs filtered by status group / type / target table. Returns up
    /// to <paramref name="limit"/> rows ordered by created_at DESC.
    /// </summary>
    public async Task<IReadOnlyList<DatasetJobRecord>> ListAsync(
        string? statusGroup,
        string? type,
        string? targetTable,
        int limit,
        CancellationToken ct = default)
    {
        // statusGroup: "active" → queued+running; "terminal" → succeeded+failed+canceled+skipped;
        // null/"all" → everything.
        var where = new List<string>();
        var args = new DynamicParameters();

        if (string.Equals(statusGroup, "active", StringComparison.OrdinalIgnoreCase))
            where.Add("status IN ('queued','running')");
        else if (string.Equals(statusGroup, "terminal", StringComparison.OrdinalIgnoreCase))
            where.Add("status IN ('succeeded','failed','canceled','skipped')");

        if (!string.IsNullOrWhiteSpace(type))
        {
            where.Add("type = @Type");
            args.Add("Type", type);
        }
        if (!string.IsNullOrWhiteSpace(targetTable))
        {
            where.Add("target_table = @TargetTable");
            args.Add("TargetTable", targetTable);
        }

        var whereClause = where.Count > 0 ? "WHERE " + string.Join(" AND ", where) : "";
        var safeLimit = Math.Clamp(limit, 1, 500);
        var sql = $"SELECT * FROM dataset_jobs {whereClause} ORDER BY created_at DESC LIMIT {safeLimit}";

        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<dynamic>(new CommandDefinition(sql, args, cancellationToken: ct));
        return rows.Select(r => MapJob((object)r)).ToList();
    }

    // ── Cancel ────────────────────────────────────────────────────────────

    /// <summary>
    /// Mark a job for cancellation. Idempotent. Returns false if the job is
    /// already terminal or does not exist.
    /// </summary>
    public async Task<bool> RequestCancelAsync(Guid jobId, CancellationToken ct = default)
    {
        const string sql = """
            UPDATE dataset_jobs
               SET cancel_requested = TRUE,
                   updated_at       = now()
             WHERE job_id = @JobId
               AND status IN ('queued', 'running')
            """;
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.ExecuteAsync(new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
        return rows > 0;
    }

    // ── Subtasks / stages read ────────────────────────────────────────────

    public async Task<IReadOnlyList<DatasetJobSubtaskRecord>> GetSubtasksAsync(
        Guid jobId, CancellationToken ct = default)
    {
        const string sql = "SELECT * FROM dataset_job_subtasks WHERE job_id = @JobId ORDER BY idx";
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<dynamic>(
            new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
        return rows.Select(r => MapSubtask((object)r)).ToList();
    }

    public async Task<IReadOnlyList<DatasetJobStageRecord>> GetStagesAsync(
        Guid jobId, CancellationToken ct = default)
    {
        const string sql = "SELECT * FROM dataset_job_stages WHERE job_id = @JobId ORDER BY started_at";
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<dynamic>(
            new CommandDefinition(sql, new { JobId = jobId }, cancellationToken: ct));
        return rows.Select(r => MapStage((object)r)).ToList();
    }

    // ── Mappers (dynamic → record) ────────────────────────────────────────
    //
    // We use dynamic-row mapping rather than Dapper's column-name mapper so
    // we can be precise about NUMERIC/JSONB casts without sprinkling the
    // codebase with Dapper.SqlMapper.AddTypeHandler hooks.

    internal static DatasetJobRecord MapJobPublic(object row) => MapJob(row);

    private static DatasetJobRecord MapJob(dynamic row)
    {
        var r = (IDictionary<string, object?>)row;
        return new DatasetJobRecord(
            JobId:           (Guid)r["job_id"]!,
            Type:            (string)r["type"]!,
            ConflictClass:   (string)r["conflict_class"]!,
            TargetTable:     r["target_table"]     as string,
            TargetSymbol:    r["target_symbol"]    as string,
            TargetTimeframe: r["target_timeframe"] as string,
            TargetStartMs:   r["target_start_ms"]  as long?,
            TargetEndMs:     r["target_end_ms"]    as long?,
            ParamsJson:      JsonElementToString(r["params_json"]),
            ParamsHash:      (string)r["params_hash"]!,
            Status:          (string)r["status"]!,
            Stage:           r["stage"]            as string,
            Progress:        Convert.ToInt16(r["progress"] ?? (short)0),
            Detail:          r["detail"]           as string,
            Total:           Convert.ToInt64(r["total"]     ?? 0L),
            Completed:       Convert.ToInt64(r["completed"] ?? 0L),
            Failed:          Convert.ToInt64(r["failed"]    ?? 0L),
            Skipped:         Convert.ToInt64(r["skipped"]   ?? 0L),
            ErrorCode:       r["error_code"]    as string,
            ErrorMessage:    r["error_message"] as string,
            CancelRequested: (bool)(r["cancel_requested"] ?? false),
            CreatedBy:       r["created_by"]    as string,
            CreatedAt:       (DateTime)r["created_at"]!,
            UpdatedAt:       (DateTime)r["updated_at"]!,
            StartedAt:       r["started_at"]    as DateTime?,
            FinishedAt:      r["finished_at"]   as DateTime?);
    }

    private static DatasetJobSubtaskRecord MapSubtask(dynamic row)
    {
        var r = (IDictionary<string, object?>)row;
        return new DatasetJobSubtaskRecord(
            SubtaskId:    (Guid)r["subtask_id"]!,
            JobId:        (Guid)r["job_id"]!,
            Idx:          Convert.ToInt32(r["idx"] ?? 0),
            Label:        (string)r["label"]!,
            TargetTable:  r["target_table"] as string,
            Status:       (string)r["status"]!,
            Progress:     Convert.ToInt16(r["progress"] ?? (short)0),
            Detail:       r["detail"] as string,
            Total:        Convert.ToInt64(r["total"]     ?? 0L),
            Completed:    Convert.ToInt64(r["completed"] ?? 0L),
            Failed:       Convert.ToInt64(r["failed"]    ?? 0L),
            Skipped:      Convert.ToInt64(r["skipped"]   ?? 0L),
            ErrorCode:    r["error_code"]    as string,
            ErrorMessage: r["error_message"] as string,
            StartedAt:    r["started_at"]    as DateTime?,
            FinishedAt:   r["finished_at"]   as DateTime?);
    }

    private static DatasetJobStageRecord MapStage(dynamic row)
    {
        var r = (IDictionary<string, object?>)row;
        return new DatasetJobStageRecord(
            StageId:           (Guid)r["stage_id"]!,
            JobId:             (Guid)r["job_id"]!,
            SubtaskId:         r["subtask_id"] as Guid?,
            Name:              (string)r["name"]!,
            StartedAt:         (DateTime)r["started_at"]!,
            EndedAt:           r["ended_at"] as DateTime?,
            ItemsProcessed:    r["items_processed"] as long?,
            CustomMetricsJson: JsonElementToStringOrNull(r["custom_metrics"]));
    }

    private static string JsonElementToString(object? raw) =>
        JsonElementToStringOrNull(raw) ?? "{}";

    private static string? JsonElementToStringOrNull(object? raw)
    {
        if (raw is null) return null;
        // Npgsql returns jsonb as string by default.
        if (raw is string s) return s;
        if (raw is JsonElement el) return el.GetRawText();
        return raw.ToString();
    }
}
