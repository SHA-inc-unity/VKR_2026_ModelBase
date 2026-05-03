namespace DataService.API.Dataset;

/// <summary>
/// Port of Python microservice_data/app/backend/dataset/constants.py
/// </summary>
public static class DatasetConstants
{
    public const string BybitBaseUrl = "https://api.bybit.com";
    /// <summary>Timeout for a single HTTP GET request to Bybit. Raised from 20s to 90s
    /// so large page batches do not time out on slow uplinks.</summary>
    public const int RequestTimeoutSeconds = 90;
    /// <summary>Max retry attempts per Bybit HTTP call. Raised from 4 to 8 to
    /// survive transient network hiccups and brief rate-limit storms.</summary>
    public const int MaxRetries = 8;
    public const int PageLimitKline = 1000;
    public const int PageLimitFunding = 200;
    public const int PageLimitOpenInterest = 200;
    public const int UpsertBatchSize = 50_000;
    /// <summary>Default parallel page-fetch windows per ingest call. Reduced from 20
    /// to 8 to leave room for multiple concurrent ingest jobs on shared rate limit.</summary>
    public const int MaxParallelApiWorkers = 8;
    /// <summary>Max parallel windows for heavy timeframes (1m, 3m). Very low to
    /// avoid exhausting the shared 96 r/s Bybit budget on a single large fetch.</summary>
    public const int MaxParallelApiWorkers1m = 2;
    public const int DefaultWarmupCandles = 24;

    /// <summary>Timeframes that produce very large page counts per full-range fetch
    /// and therefore need reduced parallelism and serialized ingest scheduling.</summary>
    public static readonly IReadOnlySet<string> HeavyTimeframes =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "1m", "3m" };

    /// <summary>timeframe key → (bybit_interval, step_ms)</summary>
    public static readonly IReadOnlyDictionary<string, (string Interval, long StepMs)> Timeframes =
        new Dictionary<string, (string, long)>
        {
            ["1m"]   = ("1",   60_000),
            ["3m"]   = ("3",   180_000),
            ["5m"]   = ("5",   300_000),
            ["15m"]  = ("15",  900_000),
            ["30m"]  = ("30",  1_800_000),
            ["60m"]  = ("60",  3_600_000),
            ["120m"] = ("120", 7_200_000),
            ["240m"] = ("240", 14_400_000),
            ["360m"] = ("360", 21_600_000),
            ["720m"] = ("720", 43_200_000),
            ["1d"]   = ("D",   86_400_000),
        };

    /// <summary>bybit_interval → step_ms</summary>
    public static readonly IReadOnlyDictionary<string, long> IntervalToStepMs =
        Timeframes.ToDictionary(kv => kv.Value.Interval, kv => kv.Value.StepMs);

    public static readonly IReadOnlyDictionary<string, string> TimeframeAliases =
        new Dictionary<string, string>
        {
            ["1"] = "1m", ["3"] = "3m", ["5"] = "5m", ["15"] = "15m",
            ["30"] = "30m", ["60"] = "60m", ["1h"] = "60m",
            ["120"] = "120m", ["2h"] = "120m", ["240"] = "240m", ["4h"] = "240m",
            ["360"] = "360m", ["6h"] = "360m", ["720"] = "720m", ["12h"] = "720m",
            ["d"] = "1d",
        };

    /// <summary>(label, interval_ms) pairs for open interest intervals.</summary>
    public static readonly IReadOnlyList<(string Label, long IntervalMs)> OpenInterestIntervals =
        new List<(string, long)>
        {
            ("5min", 300_000), ("15min", 900_000), ("30min", 1_800_000),
            ("1h", 3_600_000), ("4h", 14_400_000), ("1d", 86_400_000),
        };

    public static readonly int[] RollingWindows = { 6, 24 };
    public static readonly int[] ReturnHorizons = { 1, 6, 24 };
    public static readonly int[] RsiLagSteps = { 1, 2 };

    /// <summary>Raw dataset schema (column_name → SQL type) — 13 columns written by ingest.</summary>
    public static readonly IReadOnlyList<(string Column, string SqlType)> RawTableSchema =
        new List<(string, string)>
        {
            ("timestamp_utc", "timestamp with time zone"),
            ("symbol", "character varying"),
            ("exchange", "character varying"),
            ("timeframe", "character varying"),
            // OHLC tuple — all four prices come from a single Bybit kline
            // (`/v5/market/kline`) and are written together by ingest.
            // `close_price` was historically named `index_price`; it always
            // stored the candle close, never a separate index series.
            ("open_price",  "numeric"),
            ("high_price",  "numeric"),
            ("low_price",   "numeric"),
            ("close_price", "numeric"),
            ("volume",      "numeric"),
            ("turnover",    "numeric"),
            ("funding_rate", "numeric"),
            ("open_interest", "numeric"),
            ("rsi", "numeric"),
        };

    /// <summary>Approved feature columns (27 cols) computed via SQL window functions
    /// after ingest. All are nullable <c>double precision</c>.</summary>
    public static readonly IReadOnlyList<(string Column, string SqlType)> FeatureTableSchema =
        BuildFeatureTableSchema();

    private static IReadOnlyList<(string Column, string SqlType)> BuildFeatureTableSchema()
    {
        var list = new List<(string, string)>();
        foreach (var h in ReturnHorizons) list.Add(($"return_{h}", "double precision"));
        foreach (var h in ReturnHorizons) list.Add(($"log_return_{h}", "double precision"));
        foreach (var w in RollingWindows)
        {
            list.Add(($"price_roll{w}_mean", "double precision"));
            list.Add(($"price_roll{w}_std",  "double precision"));
            list.Add(($"price_roll{w}_min",  "double precision"));
            list.Add(($"price_roll{w}_max",  "double precision"));
        }
        foreach (var w in RollingWindows) list.Add(($"price_to_roll{w}_mean", "double precision"));
        foreach (var w in RollingWindows) list.Add(($"price_vol_{w}",         "double precision"));
        foreach (var w in RollingWindows) list.Add(($"oi_roll{w}_mean",       "double precision"));
        list.Add(("oi_return_1", "double precision"));
        foreach (var k in RsiLagSteps) list.Add(($"rsi_lag_{k}", "double precision"));
        list.Add(("hour_sin", "double precision"));
        list.Add(("hour_cos", "double precision"));
        list.Add(("dow_sin",  "double precision"));
        list.Add(("dow_cos",  "double precision"));
        // OHLCV-derived features
        foreach (var w in RollingWindows) list.Add(($"atr_{w}",              "double precision"));
        list.Add(("candle_body",      "double precision"));
        list.Add(("upper_wick",       "double precision"));
        list.Add(("lower_wick",       "double precision"));
        foreach (var w in RollingWindows) list.Add(($"volume_roll{w}_mean", "double precision"));
        foreach (var w in RollingWindows) list.Add(($"volume_to_roll{w}_mean", "double precision"));
        list.Add(("volume_return_1",  "double precision"));
        list.Add(("rsi_slope",        "double precision"));
        return list;
    }

    /// <summary>Full dataset schema = raw + features (35 columns total).</summary>
    public static readonly IReadOnlyList<(string Column, string SqlType)> FullTableSchema =
        RawTableSchema.Concat(FeatureTableSchema).ToList();
}
