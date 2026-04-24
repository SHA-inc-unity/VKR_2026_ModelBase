namespace DataService.API.Dataset;

/// <summary>
/// Port of Python microservice_data/app/backend/dataset/constants.py
/// </summary>
public static class DatasetConstants
{
    public const string BybitBaseUrl = "https://api.bybit.com";
    public const int RequestTimeoutSeconds = 20;
    public const int MaxRetries = 4;
    public const int PageLimitKline = 1000;
    public const int PageLimitFunding = 200;
    public const int PageLimitOpenInterest = 200;
    public const int UpsertBatchSize = 50_000;
    public const int MaxParallelApiWorkers = 20;
    public const int DefaultWarmupCandles = 24;

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

    public static readonly int[] LagSteps = { 1, 2, 3, 6, 12, 24 };
    public static readonly int[] RollingWindows = { 6, 24 };
    public static readonly int[] ReturnHorizons = { 1, 6, 24 };
    public static readonly int[] FundingLagSteps = { 1, 2, 3 };
    public static readonly int[] OiLagSteps = { 1, 2, 3 };
    public static readonly int[] RsiLagSteps = { 1, 2 };

    /// <summary>Expected dataset table schema (column_name → SQL type).</summary>
    public static readonly IReadOnlyList<(string Column, string SqlType)> RawTableSchema =
        new List<(string, string)>
        {
            ("timestamp_utc", "timestamp with time zone"),
            ("symbol", "character varying"),
            ("exchange", "character varying"),
            ("timeframe", "character varying"),
            ("index_price", "numeric"),
            ("funding_rate", "numeric"),
            ("open_interest", "numeric"),
            ("rsi", "numeric"),
        };
}
