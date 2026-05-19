using System.Diagnostics;
using System.Text.Json;
using Confluent.Kafka;
using GatewayService.API.DTOs;
using GatewayService.API.Filters;
using GatewayService.API.Kafka;
using GatewayService.API.Middleware;
using GatewayService.API.Settings;
using Microsoft.AspNetCore.Cors;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Controllers;

/// <summary>
/// Admin backend facade — explicit HTTP endpoints for every admin Kafka command.
///
/// All routes require the shared-secret token validated by AdminApiKeyFilter.
/// The gateway is the only service that speaks Kafka; the admin Next.js process
/// uses plain HTTPS to reach these endpoints and never touches Kafka directly.
///
/// Endpoint → Kafka topic mapping:
///   POST /api/admin/health/data                   → cmd.data.health
///   POST /api/admin/health/analytics              → cmd.analytics.health
///   POST /api/admin/dataset/list-tables           → cmd.data.dataset.list_tables
///   POST /api/admin/dataset/coverage              → cmd.data.dataset.coverage
///   POST /api/admin/dataset/rows                  → cmd.data.dataset.rows
///   POST /api/admin/dataset/export                → cmd.data.dataset.export
///   POST /api/admin/dataset/ingest                → cmd.data.dataset.ingest
///   POST /api/admin/dataset/normalize-timeframe   → cmd.data.dataset.normalize_timeframe
///   POST /api/admin/dataset/make-table-name       → cmd.data.dataset.make_table_name
///   POST /api/admin/dataset/instrument-details    → cmd.data.dataset.instrument_details
///   POST /api/admin/dataset/schema                → cmd.data.dataset.table_schema
///   POST /api/admin/dataset/find-missing          → cmd.data.dataset.find_missing
///   POST /api/admin/dataset/timestamps            → cmd.data.dataset.timestamps
///   POST /api/admin/dataset/constants             → cmd.data.dataset.constants
///   POST /api/admin/dataset/delete-rows           → cmd.data.dataset.delete_rows
///   POST /api/admin/dataset/import-csv            → cmd.data.dataset.import_csv
///   POST /api/admin/dataset/upsert-ohlcv          → cmd.data.dataset.upsert_ohlcv
///   POST /api/admin/dataset/column-stats          → cmd.data.dataset.column_stats
///   POST /api/admin/dataset/column-histogram      → cmd.data.dataset.column_histogram
///   POST /api/admin/dataset/browse                → cmd.data.dataset.browse
///   POST /api/admin/dataset/compute-features      → cmd.data.dataset.compute_features
///   POST /api/admin/dataset/detect-anomalies      → cmd.data.dataset.detect_anomalies
///   POST /api/admin/dataset/clean-preview         → cmd.data.dataset.clean.preview
///   POST /api/admin/dataset/clean-apply           → cmd.data.dataset.clean.apply
///   POST /api/admin/dataset/audit-log             → cmd.data.dataset.audit_log
///   POST /api/admin/dataset/jobs/start            → cmd.data.dataset.jobs.start
///   POST /api/admin/dataset/jobs/cancel           → cmd.data.dataset.jobs.cancel
///   POST /api/admin/dataset/jobs/get              → cmd.data.dataset.jobs.get
///   POST /api/admin/dataset/jobs/list             → cmd.data.dataset.jobs.list
///   POST /api/admin/dataset/db-ping               → cmd.data.db.ping
///   POST /api/admin/analytic/dataset/load         → cmd.analitic.dataset.load
///   POST /api/admin/analytic/dataset/unload       → cmd.analitic.dataset.unload
///   POST /api/admin/analytic/dataset/status       → cmd.analitic.dataset.status
///   POST /api/admin/analytic/anomaly/dbscan       → cmd.analitic.anomaly.dbscan
///   POST /api/admin/analytic/anomaly/isolation-forest → cmd.analitic.anomaly.isolation_forest
///   POST /api/admin/analytic/dataset/distribution → cmd.analitic.dataset.distribution
///   POST /api/admin/analytic/dataset/quality-check → cmd.analitic.dataset.quality_check
///   POST /api/admin/analytic/dataset/load-ohlcv   → cmd.analitic.dataset.load_ohlcv
///   POST /api/admin/analytic/dataset/recompute-features → cmd.analitic.dataset.recompute_features
///   POST /api/admin/analytics/train/start         → cmd.analytics.train.start
///   POST /api/admin/analytics/train/status        → cmd.analytics.train.status
///   POST /api/admin/analytics/model/list          → cmd.analytics.model.list
///   POST /api/admin/analytics/model/load          → cmd.analytics.model.load
///   POST /api/admin/analytics/predict             → cmd.analytics.predict
/// </summary>
[ApiController]
[Route("api/admin")]
[DisableCors]
[ServiceFilter(typeof(AdminApiKeyFilter))]
public sealed class AdminController : ControllerBase
{
    private const string KafkaTimeoutCode = "admin_kafka_timeout";
    private const string KafkaUnavailableCode = "admin_kafka_unavailable";

    private readonly IKafkaRequestClient _kafka;
    private readonly ILogger<AdminController> _log;
    private readonly TimeSpan _defaultTimeout;
    private readonly TimeSpan _longTimeout;

    public AdminController(
        IKafkaRequestClient kafka,
        IOptions<AdminSettings> opts,
        ILogger<AdminController> log)
    {
        _kafka          = kafka;
        _log            = log;
        _defaultTimeout = TimeSpan.FromSeconds(opts.Value.DefaultTimeoutSeconds);
        _longTimeout    = TimeSpan.FromSeconds(opts.Value.LongTimeoutSeconds);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private async Task<IActionResult> Forward(
        string topic, JsonElement? body, TimeSpan timeout, CancellationToken ct)
    {
        var startedAt = Stopwatch.GetTimestamp();
        var correlationId = HttpContext.GetCorrelationId();
        var payloadKind = body.HasValue ? body.Value.ValueKind.ToString() : "empty";

        _log.LogInformation(
            "AdminFacade request start topic={Topic} path={Path} timeoutMs={TimeoutMs} payloadKind={PayloadKind} correlationId={CorrelationId}",
            topic,
            HttpContext.Request.Path,
            (int)timeout.TotalMilliseconds,
            payloadKind,
            correlationId);

        try
        {
            var result = await _kafka.RequestAsync(
                topic, body.HasValue ? (object)body.Value : new { }, timeout, ct);
            _log.LogInformation(
                "AdminFacade request success topic={Topic} path={Path} durationMs={DurationMs} correlationId={CorrelationId}",
                topic,
                HttpContext.Request.Path,
                (int)Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds,
                correlationId);
            return Ok(result);
        }
        catch (TimeoutException tex)
        {
            _log.LogWarning(tex,
                "AdminFacade request timeout topic={Topic} path={Path} timeoutMs={TimeoutMs} durationMs={DurationMs} correlationId={CorrelationId}",
                topic,
                HttpContext.Request.Path,
                (int)timeout.TotalMilliseconds,
                (int)Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds,
                correlationId);
            return StatusCode(504, ErrorResponse.AdminTimeout(
                KafkaTimeoutCode,
                tex.Message,
                correlationId));
        }
        catch (Exception ex) when (IsKafkaTransportFailure(ex))
        {
            const string detail = "Gateway could not publish the Kafka request. Check Redpanda/Kafka broker connectivity and the bootstrap listener.";

            _log.LogError(ex,
                "AdminFacade request kafka-unavailable topic={Topic} path={Path} durationMs={DurationMs} correlationId={CorrelationId}",
                topic,
                HttpContext.Request.Path,
                (int)Stopwatch.GetElapsedTime(startedAt).TotalMilliseconds,
                correlationId);
            return StatusCode(503, ErrorResponse.AdminUnavailable(
                KafkaUnavailableCode,
                detail,
                correlationId));
        }
    }

    private static bool IsKafkaTransportFailure(Exception ex) =>
        ex is KafkaException
        or ProduceException<string, string>
        or TaskCanceledException;

    private Task<IActionResult> Forward(string topic, JsonElement? body, CancellationToken ct)
        => Forward(topic, body, _defaultTimeout, ct);

    // ── Health ────────────────────────────────────────────────────────────────

    [HttpPost("health/data")]
    public Task<IActionResult> HealthData([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DataHealth, body, ct);

    [HttpPost("health/analytics")]
    public Task<IActionResult> HealthAnalytics([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnalyticsHealth, body, ct);

    // ── Dataset ───────────────────────────────────────────────────────────────

    [HttpPost("dataset/list-tables")]
    public Task<IActionResult> DatasetListTables([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetListTables, body, ct);

    [HttpPost("dataset/coverage")]
    public Task<IActionResult> DatasetCoverage([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetCoverage, body, ct);

    [HttpPost("dataset/rows")]
    public Task<IActionResult> DatasetRows([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetRows, body, ct);

    [HttpPost("dataset/export")]
    public Task<IActionResult> DatasetExport([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetExport, body, _longTimeout, ct);

    [HttpPost("dataset/ingest")]
    public Task<IActionResult> DatasetIngest([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetIngest, body, _longTimeout, ct);

    [HttpPost("dataset/normalize-timeframe")]
    public Task<IActionResult> DatasetNormalizeTf([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetNormalizeTf, body, ct);

    [HttpPost("dataset/make-table-name")]
    public Task<IActionResult> DatasetMakeTable([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetMakeTable, body, ct);

    [HttpPost("dataset/instrument-details")]
    public Task<IActionResult> DatasetInstrument([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetInstrument, body, ct);

    [HttpPost("dataset/schema")]
    public Task<IActionResult> DatasetSchema([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetSchema, body, ct);

    [HttpPost("dataset/find-missing")]
    public Task<IActionResult> DatasetFindMissing([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetFindMissing, body, ct);

    [HttpPost("dataset/timestamps")]
    public Task<IActionResult> DatasetTimestamps([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetTimestamps, body, ct);

    [HttpPost("dataset/constants")]
    public Task<IActionResult> DatasetConstants([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetConstants, body, ct);

    [HttpPost("dataset/delete-rows")]
    public Task<IActionResult> DatasetDeleteRows([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetDeleteRows, body, ct);

    [HttpPost("dataset/import-csv")]
    public Task<IActionResult> DatasetImportCsv([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetImportCsv, body, _longTimeout, ct);

    [HttpPost("dataset/upsert-ohlcv")]
    public Task<IActionResult> DatasetUpsertOhlcv([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetUpsertOhlcv, body, ct);

    // ── Anomaly / inspection ──────────────────────────────────────────────────

    [HttpPost("dataset/column-stats")]
    public Task<IActionResult> DatasetColumnStats([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetColumnStats, body, ct);

    [HttpPost("dataset/column-histogram")]
    public Task<IActionResult> DatasetColumnHistogram([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetColumnHistogram, body, ct);

    [HttpPost("dataset/browse")]
    public Task<IActionResult> DatasetBrowse([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetBrowse, body, ct);

    [HttpPost("dataset/compute-features")]
    public Task<IActionResult> DatasetComputeFeatures([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetComputeFeatures, body, _longTimeout, ct);

    [HttpPost("dataset/detect-anomalies")]
    public Task<IActionResult> DatasetDetectAnomalies([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetDetectAnomalies, body, _longTimeout, ct);

    [HttpPost("dataset/clean-preview")]
    public Task<IActionResult> DatasetCleanPreview([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetCleanPreview, body, ct);

    [HttpPost("dataset/clean-apply")]
    public Task<IActionResult> DatasetCleanApply([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetCleanApply, body, _longTimeout, ct);

    [HttpPost("dataset/audit-log")]
    public Task<IActionResult> DatasetAuditLog([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DatasetAuditLog, body, ct);

    // ── Background jobs ───────────────────────────────────────────────────────

    [HttpPost("dataset/jobs/start")]
    public Task<IActionResult> JobsStart([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.JobsStart, body, ct);

    [HttpPost("dataset/jobs/cancel")]
    public Task<IActionResult> JobsCancel([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.JobsCancel, body, ct);

    [HttpPost("dataset/jobs/get")]
    public Task<IActionResult> JobsGet([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.JobsGet, body, ct);

    [HttpPost("dataset/jobs/list")]
    public Task<IActionResult> JobsList([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.JobsList, body, ct);

    // ── DB ────────────────────────────────────────────────────────────────────

    [HttpPost("dataset/db-ping")]
    public Task<IActionResult> DbPing([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.DbPing, body, ct);

    // ── Analitic (dataset session + ML) ──────────────────────────────────────

    [HttpPost("analytic/dataset/load")]
    public Task<IActionResult> AnaliticDatasetLoad([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetLoad, body, _longTimeout, ct);

    [HttpPost("analytic/dataset/unload")]
    public Task<IActionResult> AnaliticDatasetUnload([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetUnload, body, ct);

    [HttpPost("analytic/dataset/status")]
    public Task<IActionResult> AnaliticDatasetStatus([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetStatus, body, ct);

    [HttpPost("analytic/anomaly/dbscan")]
    public Task<IActionResult> AnaliticAnomalyDbscan([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticAnomalyDbscan, body, _longTimeout, ct);

    [HttpPost("analytic/anomaly/isolation-forest")]
    public Task<IActionResult> AnaliticAnomalyIsolationForest([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticAnomalyIsolationForest, body, _longTimeout, ct);

    [HttpPost("analytic/dataset/distribution")]
    public Task<IActionResult> AnaliticDatasetDistribution([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetDistribution, body, ct);

    [HttpPost("analytic/dataset/quality-check")]
    public Task<IActionResult> AnaliticDatasetQualityCheck([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetQualityCheck, body, _longTimeout, ct);

    [HttpPost("analytic/dataset/load-ohlcv")]
    public Task<IActionResult> AnaliticDatasetLoadOhlcv([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetLoadOhlcv, body, _longTimeout, ct);

    [HttpPost("analytic/dataset/recompute-features")]
    public Task<IActionResult> AnaliticDatasetRecomputeFeatures([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnaliticDatasetRecomputeFeatures, body, _longTimeout, ct);

    // ── Analytics (train / model) ─────────────────────────────────────────────

    [HttpPost("analytics/train/start")]
    public Task<IActionResult> AnalyticsTrainStart([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnalyticsTrainStart, body, _longTimeout, ct);

    [HttpPost("analytics/train/status")]
    public Task<IActionResult> AnalyticsTrainStatus([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnalyticsTrainStatus, body, ct);

    [HttpPost("analytics/model/list")]
    public Task<IActionResult> AnalyticsModelList([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnalyticsModelList, body, ct);

    [HttpPost("analytics/model/load")]
    public Task<IActionResult> AnalyticsModelLoad([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnalyticsModelLoad, body, _longTimeout, ct);

    [HttpPost("analytics/predict")]
    public Task<IActionResult> AnalyticsPredict([FromBody] JsonElement? body, CancellationToken ct)
        => Forward(AdminTopics.AnalyticsPredict, body, _longTimeout, ct);
}
