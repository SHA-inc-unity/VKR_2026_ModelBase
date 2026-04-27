namespace DataService.API.Database;

/// <summary>
/// Phase A: contract types for dataset background jobs. Stored in
/// <c>dataset_jobs</c> / <c>dataset_job_subtasks</c> / <c>dataset_job_stages</c>.
/// All public APIs (Kafka topics, admin UI) speak these shapes.
/// </summary>
public static class DatasetJobStatus
{
    public const string Queued    = "queued";
    public const string Running   = "running";
    public const string Succeeded = "succeeded";
    public const string Failed    = "failed";
    public const string Canceled  = "canceled";
    public const string Skipped   = "skipped";

    public static bool IsTerminal(string status) =>
        status == Succeeded || status == Failed || status == Canceled || status == Skipped;

    public static bool IsActive(string status) =>
        status == Queued || status == Running;
}

public static class DatasetJobType
{
    public const string Ingest           = "ingest";
    public const string DetectAnomalies  = "detect_anomalies";
    public const string ComputeFeatures  = "compute_features";
    public const string CleanApply       = "clean_apply";
    public const string Export           = "export";
    public const string ImportCsv        = "import_csv";
    public const string UpsertOhlcv      = "upsert_ohlcv";

    public static readonly HashSet<string> All = new(StringComparer.OrdinalIgnoreCase)
    {
        Ingest, DetectAnomalies, ComputeFeatures, CleanApply,
        Export, ImportCsv, UpsertOhlcv,
    };

    /// <summary>
    /// Map job-type → conflict class. Two active jobs in the same conflict
    /// class on the same table cannot run simultaneously; <see cref="JobLockManager"/>
    /// (Phase B) enforces this. Read-only jobs use <c>read_heavy</c>.
    /// </summary>
    public static string ConflictClassOf(string type) => type switch
    {
        Ingest          => DatasetJobConflictClass.MutatingTable,
        UpsertOhlcv     => DatasetJobConflictClass.MutatingTable,
        ImportCsv       => DatasetJobConflictClass.MutatingTable,
        CleanApply      => DatasetJobConflictClass.MutatingTable,
        ComputeFeatures => DatasetJobConflictClass.MutatingTable,
        DetectAnomalies => DatasetJobConflictClass.ReadHeavy,
        Export          => DatasetJobConflictClass.ReadHeavy,
        _               => DatasetJobConflictClass.ReadHeavy,
    };
}

public static class DatasetJobConflictClass
{
    /// <summary>Writes raw/feature columns of a market-data table.</summary>
    public const string MutatingTable = "mutating_table";
    /// <summary>Heavy read pipeline (e.g. anomaly scan, full export).</summary>
    public const string ReadHeavy     = "read_heavy";
    /// <summary>Marker for future external-IO heavy ops (e.g. Bybit fetch only).</summary>
    public const string ExternalIo    = "external_io";
}

/// <summary>Snapshot of a row in <c>dataset_jobs</c>.</summary>
public sealed record DatasetJobRecord(
    Guid     JobId,
    string   Type,
    string   ConflictClass,
    string?  TargetTable,
    string?  TargetSymbol,
    string?  TargetTimeframe,
    long?    TargetStartMs,
    long?    TargetEndMs,
    string   ParamsJson,
    string   ParamsHash,
    string   Status,
    string?  Stage,
    short    Progress,
    string?  Detail,
    long     Total,
    long     Completed,
    long     Failed,
    long     Skipped,
    string?  ErrorCode,
    string?  ErrorMessage,
    bool     CancelRequested,
    string?  CreatedBy,
    DateTime CreatedAt,
    DateTime UpdatedAt,
    DateTime? StartedAt,
    DateTime? FinishedAt);

/// <summary>Snapshot of a row in <c>dataset_job_subtasks</c>.</summary>
public sealed record DatasetJobSubtaskRecord(
    Guid     SubtaskId,
    Guid     JobId,
    int      Idx,
    string   Label,
    string?  TargetTable,
    string   Status,
    short    Progress,
    string?  Detail,
    long     Total,
    long     Completed,
    long     Failed,
    long     Skipped,
    string?  ErrorCode,
    string?  ErrorMessage,
    DateTime? StartedAt,
    DateTime? FinishedAt);

/// <summary>Snapshot of a row in <c>dataset_job_stages</c>.</summary>
public sealed record DatasetJobStageRecord(
    Guid     StageId,
    Guid     JobId,
    Guid?    SubtaskId,
    string   Name,
    DateTime StartedAt,
    DateTime? EndedAt,
    long?    ItemsProcessed,
    string?  CustomMetricsJson);

/// <summary>
/// Input parameters for <c>cmd.data.dataset.jobs.start</c>. All fields are
/// optional except <see cref="Type"/> and <see cref="ParamsJson"/>; the
/// scheduler uses <see cref="ParamsHash"/> for dedup of identical active jobs.
/// </summary>
public sealed record DatasetJobStartRequest(
    string   Type,
    string?  TargetTable,
    string?  TargetSymbol,
    string?  TargetTimeframe,
    long?    TargetStartMs,
    long?    TargetEndMs,
    string   ParamsJson,
    string   ParamsHash,
    string?  CreatedBy);
