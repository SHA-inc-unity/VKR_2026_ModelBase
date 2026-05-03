using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using DataService.API.Database;
using Npgsql;

namespace DataService.API.Kafka;

/// <summary>
/// Phase A: minimal request/reply handlers for the Dataset jobs control
/// plane. They persist rows in <c>dataset_jobs</c> via
/// <see cref="DatasetJobsRepository"/>; the actual job runner that
/// transitions queued → running → terminal is added in Phase B.
/// </summary>
public sealed partial class KafkaConsumerService
{
    private async Task<object> HandleJobsStartAsync(JsonElement payload, CancellationToken ct)
    {
        // Fast precheck: if the schema is not yet ensured (DB was unreachable
        // at startup, or service is still warming up), reply immediately with
        // a typed error code instead of letting the caller hit a generic
        // 10-second Kafka timeout.
        if (!_jobsRepo.SchemaReady)
            return new
            {
                error = "dataset jobs schema not ready — DB may be unreachable",
                code  = "schema_not_ready",
            };

        var type = TryGetString(payload, "type");
        if (string.IsNullOrWhiteSpace(type))
            return new { error = "missing required field: type", code = "bad_request" };
        if (!DatasetJobType.All.Contains(type))
            return new { error = $"unknown job type: {type}", code = "bad_request" };

        // The params subobject is opaque to Phase A — we only persist it
        // and use a stable hash for dedup. Job-type-specific schemas are
        // validated in Phase C when the runner picks them up.
        var paramsElement = payload.TryGetProperty("params", out var pe)
                            && pe.ValueKind == JsonValueKind.Object
            ? pe
            : default;
        var paramsJson = paramsElement.ValueKind == JsonValueKind.Object
            ? paramsElement.GetRawText()
            : "{}";

        // For ingest jobs we ALWAYS know the target table from
        // (target_symbol, target_timeframe) — derive it here so the
        // scheduler can take a per-table lock and run ingests for
        // different symbols/timeframes in parallel. Without this every
        // ingest would share the same global "external_io::*" lock and
        // serialize end-to-end.
        var rawTargetTable    = TryGetString(payload, "target_table");
        var rawTargetSymbol   = TryGetString(payload, "target_symbol");
        var rawTargetTimeframe = TryGetString(payload, "target_timeframe");
        string? effectiveTargetTable = rawTargetTable;
        if (string.IsNullOrWhiteSpace(effectiveTargetTable)
            && type == DatasetJobType.Ingest
            && !string.IsNullOrWhiteSpace(rawTargetSymbol)
            && !string.IsNullOrWhiteSpace(rawTargetTimeframe))
        {
            try
            {
                effectiveTargetTable = Dataset.DatasetCore.MakeTableName(
                    rawTargetSymbol!, rawTargetTimeframe!);
            }
            catch (ArgumentException)
            {
                // Invalid symbol/timeframe — fall through to params-validation
                // path which produces a typed bad_request reply.
            }
        }

        var paramsHash = ComputeParamsHash(
            type,
            effectiveTargetTable,
            rawTargetSymbol,
            rawTargetTimeframe,
            TryGetInt64(payload, "target_start_ms"),
            TryGetInt64(payload, "target_end_ms"),
            paramsJson);

        var req = new DatasetJobStartRequest(
            Type:            type,
            TargetTable:     effectiveTargetTable,
            TargetSymbol:    rawTargetSymbol,
            TargetTimeframe: rawTargetTimeframe,
            TargetStartMs:   TryGetInt64(payload, "target_start_ms"),
            TargetEndMs:     TryGetInt64(payload, "target_end_ms"),
            ParamsJson:      paramsJson,
            ParamsHash:      paramsHash,
            CreatedBy:       TryGetString(payload, "created_by"));

        try
        {
            var (job, deduped) = await _jobsRepo.StartAsync(req, ct);
            return new
            {
                job_id  = job.JobId,
                status  = job.Status,
                deduped,
                job     = SerializeJob(job),
            };
        }
        catch (ArgumentException ax)
        {
            return new { error = ax.Message, code = "bad_request" };
        }
        catch (InvalidOperationException iox)
        {
            // Schema-not-ready or "just-inserted job not found" race.
            _log.LogError(iox, "JobsStart rejected (invalid state)");
            return new { error = iox.Message, code = "invalid_state" };
        }
        catch (PostgresException px)
        {
            // Any other DB-level failure (constraint violation, undefined
            // column, etc.). Reply explicitly so the caller sees the cause
            // instead of a generic Kafka timeout.
            _log.LogError(px,
                "JobsStart DB error: SQLSTATE={SqlState} {Message}",
                px.SqlState, px.MessageText);
            return new
            {
                error = $"db error: {px.MessageText}",
                code  = $"pg_{px.SqlState}",
            };
        }
        catch (NpgsqlException nx)
        {
            _log.LogError(nx, "JobsStart connection-level DB failure");
            return new
            {
                error = $"db unavailable: {nx.Message}",
                code  = "db_unavailable",
            };
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _log.LogError(ex, "JobsStart unexpected failure");
            return new { error = ex.Message, code = "internal_error" };
        }
    }

    private async Task<object> HandleJobsCancelAsync(JsonElement payload, CancellationToken ct)
    {
        var jobIdStr = TryGetString(payload, "job_id");
        if (!Guid.TryParse(jobIdStr, out var jobId))
            return new { error = "missing or invalid job_id" };

        var ok = await _jobsRepo.RequestCancelAsync(jobId, ct);
        return new { job_id = jobId, ok };
    }

    private async Task<object> HandleJobsGetAsync(JsonElement payload, CancellationToken ct)
    {
        var jobIdStr = TryGetString(payload, "job_id");
        if (!Guid.TryParse(jobIdStr, out var jobId))
            return new { error = "missing or invalid job_id" };

        var job = await _jobsRepo.GetByIdAsync(jobId, ct);
        if (job is null)
            return new { error = "job not found", job_id = jobId };

        var subtasks = await _jobsRepo.GetSubtasksAsync(jobId, ct);
        var stages   = await _jobsRepo.GetStagesAsync(jobId, ct);

        return new
        {
            job      = SerializeJob(job),
            subtasks = subtasks.Select(SerializeSubtask).ToArray(),
            stages   = stages.Select(SerializeStage).ToArray(),
        };
    }

    private async Task<object> HandleJobsListAsync(JsonElement payload, CancellationToken ct)
    {
        var statusGroup = TryGetString(payload, "status_group");        // active|terminal|all
        var type        = TryGetString(payload, "type");
        var targetTable = TryGetString(payload, "target_table");
        var limit       = (int)(TryGetInt64(payload, "limit") ?? 100L);

        var rows = await _jobsRepo.ListAsync(statusGroup, type, targetTable, limit, ct);
        return new
        {
            jobs  = rows.Select(SerializeJob).ToArray(),
            count = rows.Count,
        };
    }

    // ── Serialization (record → snake_case dict for the wire) ────────────

    private static object SerializeJob(DatasetJobRecord j) => new
    {
        job_id            = j.JobId,
        type              = j.Type,
        conflict_class    = j.ConflictClass,
        target_table      = j.TargetTable,
        target_symbol     = j.TargetSymbol,
        target_timeframe  = j.TargetTimeframe,
        target_start_ms   = j.TargetStartMs,
        target_end_ms     = j.TargetEndMs,
        // params are echoed back as a parsed JSON object so the client does
        // not have to re-decode a string.
        @params           = ParseJsonOrEmpty(j.ParamsJson),
        params_hash       = j.ParamsHash,
        status            = j.Status,
        stage             = j.Stage,
        progress          = j.Progress,
        detail            = j.Detail,
        total             = j.Total,
        completed         = j.Completed,
        failed            = j.Failed,
        skipped           = j.Skipped,
        error_code        = j.ErrorCode,
        error_message     = j.ErrorMessage,
        cancel_requested  = j.CancelRequested,
        created_by        = j.CreatedBy,
        created_at_ms     = ToMs(j.CreatedAt),
        updated_at_ms     = ToMs(j.UpdatedAt),
        started_at_ms     = j.StartedAt is { } s ? (long?)ToMs(s) : null,
        finished_at_ms    = j.FinishedAt is { } f ? (long?)ToMs(f) : null,
    };

    private static object SerializeSubtask(DatasetJobSubtaskRecord s) => new
    {
        subtask_id   = s.SubtaskId,
        job_id       = s.JobId,
        idx          = s.Idx,
        label        = s.Label,
        target_table = s.TargetTable,
        status       = s.Status,
        progress     = s.Progress,
        detail       = s.Detail,
        total        = s.Total,
        completed    = s.Completed,
        failed       = s.Failed,
        skipped      = s.Skipped,
        error_code   = s.ErrorCode,
        error_message= s.ErrorMessage,
        started_at_ms  = s.StartedAt  is { } a ? (long?)ToMs(a) : null,
        finished_at_ms = s.FinishedAt is { } b ? (long?)ToMs(b) : null,
    };

    private static object SerializeStage(DatasetJobStageRecord s) => new
    {
        stage_id        = s.StageId,
        job_id          = s.JobId,
        subtask_id      = s.SubtaskId,
        name            = s.Name,
        started_at_ms   = ToMs(s.StartedAt),
        ended_at_ms     = s.EndedAt is { } e ? (long?)ToMs(e) : null,
        items_processed = s.ItemsProcessed,
        custom_metrics  = ParseJsonOrEmpty(s.CustomMetricsJson),
    };

    // ── Helpers ──────────────────────────────────────────────────────────

    private static long ToMs(DateTime dt) =>
        new DateTimeOffset(DateTime.SpecifyKind(dt, DateTimeKind.Utc)).ToUnixTimeMilliseconds();

    private static JsonElement ParseJsonOrEmpty(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return JsonDocument.Parse("{}").RootElement.Clone();
        try   { return JsonDocument.Parse(raw).RootElement.Clone(); }
        catch { return JsonDocument.Parse("{}").RootElement.Clone(); }
    }

    /// <summary>
    /// Stable params-hash for dedup. Hash inputs include the type, target
    /// coordinates, and the params object encoded with sorted keys so that
    /// payload field ordering does not change the hash.
    /// </summary>
    private static string ComputeParamsHash(
        string type,
        string? targetTable,
        string? targetSymbol,
        string? targetTimeframe,
        long?   targetStartMs,
        long?   targetEndMs,
        string  paramsJson)
    {
        var canonical = CanonicalizeJson(paramsJson);
        var sb = new StringBuilder();
        sb.Append(type).Append('|')
          .Append(targetTable ?? "").Append('|')
          .Append(targetSymbol ?? "").Append('|')
          .Append(targetTimeframe ?? "").Append('|')
          .Append(targetStartMs?.ToString() ?? "").Append('|')
          .Append(targetEndMs?.ToString()   ?? "").Append('|')
          .Append(canonical);

        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(sb.ToString()));
        return Convert.ToHexString(bytes);
    }

    /// <summary>
    /// Re-serialize a JSON object with deterministic key ordering. Input
    /// that is not a valid JSON object falls back to the raw text.
    /// </summary>
    private static string CanonicalizeJson(string raw)
    {
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(raw) ? "{}" : raw);
            using var ms = new MemoryStream();
            using (var w = new Utf8JsonWriter(ms))
            {
                WriteCanonical(doc.RootElement, w);
            }
            return Encoding.UTF8.GetString(ms.ToArray());
        }
        catch
        {
            return raw ?? "";
        }
    }

    private static void WriteCanonical(JsonElement el, Utf8JsonWriter w)
    {
        switch (el.ValueKind)
        {
            case JsonValueKind.Object:
                w.WriteStartObject();
                foreach (var prop in el.EnumerateObject().OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    w.WritePropertyName(prop.Name);
                    WriteCanonical(prop.Value, w);
                }
                w.WriteEndObject();
                break;
            case JsonValueKind.Array:
                w.WriteStartArray();
                foreach (var item in el.EnumerateArray()) WriteCanonical(item, w);
                w.WriteEndArray();
                break;
            default:
                el.WriteTo(w);
                break;
        }
    }
}
