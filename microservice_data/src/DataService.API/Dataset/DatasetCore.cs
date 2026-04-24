namespace DataService.API.Dataset;

/// <summary>
/// Port of Python microservice_data/app/backend/dataset/core.py
/// Utility functions: timeframe normalization, table naming, timestamp math.
/// </summary>
public static class DatasetCore
{
    /// <summary>
    /// Normalize a timeframe string → (key, bybit_interval, step_ms).
    /// Throws <see cref="ArgumentException"/> for unknown timeframes.
    /// </summary>
    public static (string Key, string Interval, long StepMs) NormalizeTimeframe(string value)
    {
        var key = value.Trim().ToLowerInvariant();
        if (DatasetConstants.TimeframeAliases.TryGetValue(key, out var aliased))
            key = aliased;
        if (!DatasetConstants.Timeframes.TryGetValue(key, out var tf))
        {
            var supported = string.Join(", ", DatasetConstants.Timeframes.Keys.OrderBy(x => x));
            throw new ArgumentException($"Unsupported timeframe '{value}'. Supported: {supported}");
        }
        return (key, tf.Interval, tf.StepMs);
    }

    /// <summary>Build a PostgreSQL table name: {symbol}_{timeframe}.</summary>
    public static string MakeTableName(string symbol, string timeframe) =>
        $"{symbol.ToLowerInvariant()}_{timeframe.ToLowerInvariant()}";

    /// <summary>Round timestamp down to candle boundary.</summary>
    public static long FloorToStep(long timestampMs, long stepMs) =>
        (timestampMs / stepMs) * stepMs;

    /// <summary>Round timestamp up to next candle boundary.</summary>
    public static long CeilToStep(long timestampMs, long stepMs) =>
        ((timestampMs + stepMs - 1) / stepMs) * stepMs;

    /// <summary>
    /// Clamp a time window to only closed candles.
    /// Returns (startMs, endMs) after alignment.
    /// </summary>
    public static (long StartMs, long EndMs) NormalizeWindow(long startMs, long endMs, long stepMs)
    {
        if (startMs >= endMs)
            throw new ArgumentException("Start timestamp must be earlier than end timestamp");
        var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        endMs = Math.Min(endMs, nowMs - stepMs);
        startMs = CeilToStep(startMs, stepMs);
        endMs = FloorToStep(endMs, stepMs);
        if (startMs > endMs)
            throw new InvalidOperationException("No closed candles in the requested window");
        return (startMs, endMs);
    }

    /// <summary>Choose the best open interest interval ≤ stepMs.</summary>
    public static (string Label, long IntervalMs) ChooseOpenInterestInterval(long stepMs)
    {
        var selected = DatasetConstants.OpenInterestIntervals[0];
        foreach (var candidate in DatasetConstants.OpenInterestIntervals)
        {
            if (candidate.IntervalMs <= stepMs)
                selected = candidate;
        }
        return selected;
    }

    /// <summary>Convert unix milliseconds to UTC DateTimeOffset.</summary>
    public static DateTimeOffset MsToDateTimeOffset(long ms) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ms);

    /// <summary>Parse an ISO-8601 string or unix-ms string to milliseconds.</summary>
    public static long ParseTimestampToMs(string value)
    {
        value = value.Trim();
        if (long.TryParse(value, out var number))
            return number >= 1_000_000_000_000L ? number : number * 1000L;
        var dt = DateTimeOffset.Parse(value, null, System.Globalization.DateTimeStyles.RoundtripKind);
        return dt.ToUnixTimeMilliseconds();
    }
}
