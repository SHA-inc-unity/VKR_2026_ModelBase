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
/// Distinguishes between a successful payload, an empty result, and a
/// claim-check response (data exists but was offloaded to the object store
/// because it exceeded the Kafka message-size limit).
/// </summary>
public sealed record RowsResult(
    IReadOnlyList<CandleRow> Rows,
    bool IsClaimCheck = false)
{
    /// <summary>Empty result — no rows and not a claim-check.</summary>
    public static readonly RowsResult Empty = new([], false);

    /// <summary>Claim-check result — data exists upstream but is too large for Kafka.</summary>
    public static readonly RowsResult ClaimCheck = new([], true);

    /// <summary>Creates a successful rows result.</summary>
    public static RowsResult From(IReadOnlyList<CandleRow> rows) => new(rows, false);
}

/// <summary>
/// Outcome of a synchronous ingest request.
/// </summary>
public sealed record IngestResult(
    bool Success,
    string TableName,
    int RowsIngested,
    string? Error = null)
{
    public static IngestResult Ok(string tableName, int rowsIngested) =>
        new(true, tableName, rowsIngested, null);

    public static IngestResult Fail(string? error, string? tableName = null) =>
        new(false, tableName ?? string.Empty, 0, error);
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
    /// Fetches OHLCV rows from the given table for the specified time range.
    /// The <paramref name="limit"/> parameter caps the data-service response so the
    /// payload stays under the Kafka message-size threshold.
    /// Returns a <see cref="RowsResult"/> whose <see cref="RowsResult.IsClaimCheck"/>
    /// is true when the data-service offloaded the response to the object store.
    /// </summary>
    Task<RowsResult> GetRowsAsync(
        string tableName, long startMs, long endMs, int limit, CancellationToken ct = default);

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
