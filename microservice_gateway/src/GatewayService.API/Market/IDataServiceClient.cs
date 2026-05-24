namespace GatewayService.API.Market;

/// <summary>
/// Coverage information for a data-service table.
/// Returned by <c>cmd.data.dataset.coverage</c>.
/// </summary>
public sealed record CoverageResult(
    bool   Exists,
    string TableName,
    long   Rows,
    long   MinTsMs,
    long   MaxTsMs,
    double CoveragePct
);

/// <summary>
/// A single OHLCV candle row as returned by the data-service rows endpoint.
/// </summary>
public sealed record CandleRow(
    long    TimestampMs,
    decimal Open,
    decimal High,
    decimal Low,
    decimal Close,
    decimal Volume,
    decimal Turnover
);

/// <summary>
/// Outcome of a <see cref="IDataServiceClient.GetRowsAsync"/> call.
/// Distinguishes between a successful payload, an empty result, a
/// claim-check response (data exists but was offloaded to the object store
/// because it exceeded the Kafka message-size limit), and an explicit
/// downstream failure.
/// </summary>
public enum RowsFetchResultStatus
{
    Success,
    Empty,
    ClaimCheck,
    Failure,
}

public sealed record RowsFetchResult(
    RowsFetchResultStatus Status,
    IReadOnlyList<CandleRow> Rows,
    string? ErrorCode = null,
    string? ErrorDetail = null)
{
    public bool HasRows => Rows.Count > 0;
    public bool IsEmpty => Status == RowsFetchResultStatus.Empty;
    public bool IsClaimCheck => Status == RowsFetchResultStatus.ClaimCheck;
    public bool IsFailure => Status == RowsFetchResultStatus.Failure;

    /// <summary>Empty result — no rows and not a claim-check.</summary>
    public static readonly RowsFetchResult Empty = new(RowsFetchResultStatus.Empty, Array.Empty<CandleRow>());

    /// <summary>Claim-check result — data exists upstream but is too large for Kafka.</summary>
    public static readonly RowsFetchResult ClaimCheck = new(RowsFetchResultStatus.ClaimCheck, Array.Empty<CandleRow>());

    /// <summary>Creates a successful rows result.</summary>
    public static RowsFetchResult From(IReadOnlyList<CandleRow> rows) =>
        rows.Count == 0 ? Empty : new(RowsFetchResultStatus.Success, rows);

    /// <summary>Creates an explicit downstream failure result.</summary>
    public static RowsFetchResult Fail(string errorCode, string? errorDetail = null) =>
        new(RowsFetchResultStatus.Failure, Array.Empty<CandleRow>(), errorCode, errorDetail);
}

/// <summary>
/// Outcome of a synchronous ingest request.
/// </summary>
public enum IngestResultStatus
{
    Success,
    InProgress,
    Failure,
}

public sealed record IngestResult(
    IngestResultStatus Status,
    string TableName,
    int RowsIngested,
    string? ErrorCode = null,
    string? ErrorDetail = null)
{
    public bool Success => Status == IngestResultStatus.Success;
    public bool IsInProgress => Status == IngestResultStatus.InProgress;
    public bool IsFailure => Status == IngestResultStatus.Failure;
    public string? Error => !string.IsNullOrWhiteSpace(ErrorDetail) ? ErrorDetail : ErrorCode;

    public static IngestResult Ok(string tableName, int rowsIngested) =>
        new(IngestResultStatus.Success, tableName, rowsIngested);

    public static IngestResult InProgress(
        string? tableName = null,
        string? errorCode = "DOWNSTREAM_TIMEOUT",
        string? errorDetail = null) =>
        new(IngestResultStatus.InProgress, tableName ?? string.Empty, 0, errorCode, errorDetail);

    public static IngestResult Fail(string? error, string? tableName = null) =>
        new(IngestResultStatus.Failure, tableName ?? string.Empty, 0, null, error);

    public static IngestResult FailWithCode(
        string errorCode,
        string? errorDetail = null,
        string? tableName = null) =>
        new(IngestResultStatus.Failure, tableName ?? string.Empty, 0, errorCode, errorDetail);
}

/// <summary>
/// Kafka client for the data-service (microservice_data).
/// All calls use the shared <see cref="GatewayService.API.Kafka.IKafkaRequestClient"/>
/// and the topics defined in <see cref="GatewayService.API.Market.DataTopics"/>.
/// </summary>
public interface IDataServiceClient
{
    /// <summary>
    /// Requests coverage info for the given symbol + timeframe.
    /// Returns null when the request fails or times out.
    /// </summary>
    Task<CoverageResult?> GetCoverageAsync(
        string symbol, string bybitInterval, CancellationToken ct = default);

    /// <summary>
    /// Fetches the newest fixed-width chart window anchored at the latest
    /// stored candle for the given symbol/timeframe.
    /// When <paramref name="columns"/> is non-null and non-empty, the data-service
    /// projects only those columns (in addition to <c>timestamp_utc</c>), which
    /// dramatically shrinks the Kafka payload for chart-only requests that don't
    /// need the feature columns.
    /// </summary>
    Task<RowsFetchResult> GetLatestWindowRowsAsync(
        string symbol,
        string bybitInterval,
        long stepMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default);

    /// <summary>
    /// Fetches OHLCV rows from the given table for the specified time range.
    /// The <paramref name="limit"/> parameter caps the data-service response so the
    /// payload stays under the Kafka message-size threshold.
    /// When <paramref name="columns"/> is non-null and non-empty, the data-service
    /// projects only those columns (in addition to <c>timestamp_utc</c>), which
    /// dramatically shrinks the Kafka payload for chart-only requests that don't
    /// need the feature columns.
    /// Returns a <see cref="RowsFetchResult"/> whose <see cref="RowsFetchResult.IsClaimCheck"/>
    /// is true when the data-service offloaded the response to the object store.
    /// </summary>
    Task<RowsFetchResult> GetRowsAsync(
        string tableName,
        long startMs,
        long endMs,
        int limit,
        IReadOnlyList<string>? columns = null,
        CancellationToken ct = default);

    /// <summary>
    /// Submits a data-service ingest job to the dataset queue and waits for its
    /// terminal state. Used by the chart path to lazily hydrate a missing window
    /// before replying while reusing the shared 4-slot job runner and per-table locks.
    /// </summary>
    Task<IngestResult> IngestAsync(
        string symbol, string bybitInterval, long startMs, long endMs, CancellationToken ct = default);

    /// <summary>
    /// Submits the same queued ingest path asynchronously (fire-and-forget).
    /// Errors are logged, not propagated. The caller is responsible for setting
    /// and clearing the ingest-lock key.
    /// </summary>
    void FireAndForgetIngest(
        string symbol, string bybitInterval, long startMs, long endMs,
        Action onComplete, Action<Exception> onError);
}
