namespace GatewayService.API.Market;

/// <summary>
/// Configuration for the market chart API — symbols, cache TTLs, Kafka timeouts,
/// ingest behaviour and coverage thresholds.
/// Bound from the "Market" appsettings section.
/// </summary>
public sealed class MarketSettings
{
    public const string SectionName = "Market";

    /// <summary>Bybit REST base URL used for symbol discovery.</summary>
    public string BybitBaseUrl { get; init; } = "https://api.bybit.com";

    /// <summary>CoinGecko REST base URL used for canonical global market stats.</summary>
    public string CoinGeckoBaseUrl { get; init; } = "https://api.coingecko.com/api/v3";

    /// <summary>
    /// Optional CoinGecko demo-tier API key. When set it is sent as the
    /// <c>x-cg-demo-api-key</c> header on per-coin metadata fetches to raise the
    /// shared-IP rate limit. Empty = use the public anonymous tier.
    /// </summary>
    public string CoinGeckoApiKey { get; init; } = string.Empty;

    /// <summary>Alternative.me base URL used for the Fear &amp; Greed index.</summary>
    public string FearGreedBaseUrl { get; init; } = "https://api.alternative.me";

    /// <summary>Default symbol returned in config and used as chart default.</summary>
    public string DefaultSymbol { get; init; } = "BTCUSDT";

    /// <summary>Default timeframe (client-facing id, e.g. "5m").</summary>
    public string DefaultTimeframe { get; init; } = "5m";

    /// <summary>Default candle count.</summary>
    public int DefaultCandleCount { get; init; } = 200;

    // ── Cache TTLs ────────────────────────────────────────────────────────

    /// <summary>How long to cache the Bybit symbol list (seconds).</summary>
    public int SymbolsCacheTtlSeconds { get; init; } = 3600;

    /// <summary>How long to cache the full config response (seconds).</summary>
    public int ConfigCacheTtlSeconds { get; init; } = 3600;

    /// <summary>Chart cache TTL for heavy timeframes (1m/3m/5m) in seconds.</summary>
    public int ChartCacheTtlHeavySeconds { get; init; } = 30;

    /// <summary>Chart cache TTL for medium timeframes (15m..240m) in seconds.</summary>
    public int ChartCacheTtlMediumSeconds { get; init; } = 120;

    /// <summary>Chart cache TTL for light timeframes (360m, 720m, 1d) in seconds.</summary>
    public int ChartCacheTtlLightSeconds { get; init; } = 300;

    /// <summary>How long to cache the linear market snapshot / ticker feed (seconds).</summary>
    public int SnapshotCacheTtlSeconds { get; init; } = 30;

    /// <summary>How long to cache the canonical global overview payload (seconds).</summary>
    public int GlobalOverviewCacheTtlSeconds { get; init; } = 180;

    /// <summary>
    /// How long to cache the per-coin supply / FDV / ATH metadata fetched from
    /// CoinGecko <c>/coins/markets</c> (seconds). Defaults to 6 h — circulating
    /// supply moves slowly and CoinGecko's shared-IP rate limit is tight, so this
    /// is deliberately long. The live price (which drives the displayed market
    /// cap) still comes from the 30 s Bybit snapshot, not from this cache.
    /// </summary>
    public int CoinMetadataCacheTtlSeconds { get; init; } = 21600;

    /// <summary>
    /// How long to cache the per-snapshot multi-window price-change map
    /// (1 h / 7 d / 30 d), computed in the gateway from our own candle store via
    /// <c>cmd.data.dataset.latest_rows</c> (seconds). Defaults to 120 s — the daily
    /// closes that anchor 7 d / 30 d move once per day and the hourly close once per
    /// hour, so a slightly stale anchor is fine; the live price that drives the
    /// percentage still comes from the 30 s Bybit snapshot. A cache-warm read issues
    /// zero Kafka calls.
    /// </summary>
    public int WindowChangeCacheTtlSeconds { get; init; } = 120;

    /// <summary>How long to cache the per-exchange MW-tracked global summary (seconds).</summary>
    public int GlobalSummaryCacheTtlSeconds { get; init; } = 30;

    /// <summary>
    /// Per-instance in-memory hot cache TTL (seconds) layered on top of the
    /// distributed cache to reduce Redis round-trips for bursty read traffic.
    /// </summary>
    public int LocalHotCacheSeconds { get; init; } = 5;

    // ── Kafka timeouts ────────────────────────────────────────────────────

    /// <summary>Kafka request timeout for lightweight data operations (seconds).</summary>
    public int KafkaTimeoutSeconds { get; init; } = 10;

    /// <summary>Kafka request timeout for the ingest pipeline (seconds).</summary>
    public int IngestKafkaTimeoutSeconds { get; init; } = 300;

    // ── Ingest / coverage ─────────────────────────────────────────────────

    /// <summary>
    /// Milliseconds to tell the client to wait before retrying
    /// after a "pending" (ingest in progress) response.
    /// </summary>
    public int IngestRetryAfterMs { get; init; } = 5000;

    /// <summary>
    /// Redis TTL (seconds) for the ingest-in-progress lock key.
    /// Should be slightly longer than IngestKafkaTimeoutSeconds.
    /// </summary>
    public int IngestLockTtlSeconds { get; init; } = 360;

    /// <summary>
    /// Coverage fraction [0..1] above which data is considered "full".
    /// Below this threshold the response is labelled "partial".
    /// </summary>
    public double FullCoverageThreshold { get; init; } = 0.99;

    /// <summary>
    /// Multiplier applied to the requested limit when computing the ingest
    /// window (end_ms - limit * step_ms * IngestWindowMultiplier).
    /// Ensures warmup candles for RSI-14 are included.
    /// </summary>
    public int IngestWindowMultiplier { get; init; } = 3;

    /// <summary>
    /// Minimum 24 h volume (USDT) for a symbol to appear in the config list.
    /// Bybit does not always expose volume for all instruments — symbols without
    /// volume data are included by default.
    /// </summary>
    public double MinSymbolVolumeUsdt { get; init; } = 1_000_000.0;

    // ── Ingest error recovery ─────────────────────────────────────────────

    /// <summary>
    /// TTL (seconds) for the ingest-lock key when the previous ingest attempt failed.
    /// Acts as a retry-cooldown to prevent tight retry storms immediately after an error.
    /// After this period the lock expires and the next request will trigger a fresh ingest.
    /// </summary>
    /// <remarks>
    /// Lowered from 30 s to 5 s — at 30 s a single transient network glitch
    /// to Bybit/Binance left every chart request on the affected
    /// symbol/timeframe returning 503 SERVICE_BUSY for half a minute. 5 s is
    /// still enough to absorb a back-to-back retry storm but won't visibly
    /// blank the chart on a sporadic error.
    /// </remarks>
    public int IngestErrorCooldownSeconds { get; init; } = 5;

    // ── Chart request queue / concurrency control ─────────────────────────

    /// <summary>
    /// Maximum number of concurrent chart downstream pipeline calls across all
    /// timeframe classes. Requests beyond this limit receive SERVICE_BUSY.
    /// </summary>
    public int QueueTotalConcurrency { get; init; } = 10;

    /// <summary>
    /// Maximum number of concurrent chart downstream pipeline calls for Heavy
    /// timeframes (1m, 3m, 5m) specifically, which hit the largest tables.
    /// Must be ≤ <see cref="QueueTotalConcurrency"/>.
    /// </summary>
    public int QueueHeavyConcurrency { get; init; } = 3;

    /// <summary>
    /// How long (seconds) a creator-slot request will wait to acquire a concurrency
    /// semaphore before giving up and returning SERVICE_BUSY.
    /// Does not affect the downstream Kafka timeout.
    /// </summary>
    public int QueueMaxWaitSeconds { get; init; } = 5;

    /// <summary>
    /// Maximum time (seconds) the chart endpoint will block while another
    /// ingest is in flight, polling the latest window for rows. When this
    /// budget expires without rows the gateway returns 503 SERVICE_BUSY
    /// rather than a "pending" success.
    /// </summary>
    public int ChartInflightWaitSeconds { get; init; } = 15;

    /// <summary>
    /// Polling interval (milliseconds) used by the chart endpoint while
    /// waiting on an inflight ingest. Data-service is now push-driven, so
    /// keep this short — most ingest hits return within a few hundred ms.
    /// </summary>
    public int ChartInflightPollMs { get; init; } = 150;
}
