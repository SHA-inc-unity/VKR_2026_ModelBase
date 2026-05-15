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
/// Kafka client for the data-service (microservice_data).
/// All calls use the shared <see cref="GatewayService.API.Kafka.KafkaRequestClient"/>
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
    /// Triggers the data-service ingest pipeline asynchronously
    /// (fire-and-forget). Errors are logged, not propagated.
    /// The caller is responsible for setting and clearing the ingest-lock key.
    /// </summary>
    void FireAndForgetIngest(
        string symbol, string bybitInterval, long startMs, long endMs,
        Action onComplete, Action<Exception> onError);
}
