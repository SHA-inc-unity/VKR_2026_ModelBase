using System.Diagnostics;

namespace DataService.API.Bybit;

/// <summary>
/// Process-local token-bucket rate limiter for Bybit's public REST API.
///
/// Bybit's public IP-level limit is 120 req/s. We default to 80 % of that
/// (96 r/s) to leave headroom for retries and shared-IP scenarios, which
/// matches the redesigned ingest behaviour in Phase D of the dataset
/// rework.
///
/// Implementation is a simple lock-protected bucket: tokens refill at
/// <c>RatePerSecond</c> tokens per second, capped at <c>Capacity</c>.
/// Callers <c>await</c> <see cref="AcquireAsync"/>, which blocks
/// (cooperatively, with a short delay loop) until a token is available
/// and reports back how long it had to wait.
/// </summary>
public sealed class BybitRateLimiter
{
    public int Capacity { get; }
    public double RatePerSecond { get; }

    private readonly object _lock = new();
    private double _tokens;
    private long _lastTicks;

    public BybitRateLimiter(int capacity = 96, double ratePerSecond = 96.0)
    {
        if (capacity <= 0) capacity = 1;
        if (ratePerSecond <= 0.0) ratePerSecond = 1.0;
        Capacity = capacity;
        RatePerSecond = ratePerSecond;
        _tokens = capacity;
        _lastTicks = Stopwatch.GetTimestamp();
    }

    /// <summary>
    /// Acquire one token, awaiting until one is available. Returns the
    /// number of milliseconds the caller spent waiting (0 if a token was
    /// available immediately).
    /// </summary>
    public async Task<long> AcquireAsync(CancellationToken ct = default)
    {
        var sw = Stopwatch.StartNew();
        while (true)
        {
            ct.ThrowIfCancellationRequested();
            double waitMs;
            lock (_lock)
            {
                Refill();
                if (_tokens >= 1.0)
                {
                    _tokens -= 1.0;
                    sw.Stop();
                    return sw.ElapsedMilliseconds;
                }
                waitMs = (1.0 - _tokens) / RatePerSecond * 1000.0;
            }
            // Cap delay so cancellation is responsive; min 5ms keeps us
            // out of a hot loop when the bucket is exhausted.
            var delay = (int)Math.Clamp(waitMs, 5, 250);
            try { await Task.Delay(delay, ct); }
            catch (TaskCanceledException) { throw new OperationCanceledException(ct); }
        }
    }

    private void Refill()
    {
        var now = Stopwatch.GetTimestamp();
        var elapsedSeconds = (now - _lastTicks) / (double)Stopwatch.Frequency;
        if (elapsedSeconds <= 0) return;
        _tokens = Math.Min(Capacity, _tokens + elapsedSeconds * RatePerSecond);
        _lastTicks = now;
    }
}

/// <summary>
/// Bybit business-level error: HTTP succeeded but <c>retCode != 0</c>.
/// These are not retried — a non-zero retCode from Bybit is a deterministic
/// rejection (bad symbol, parameter out of range, account problem, …).
/// </summary>
public sealed class BybitApiException : Exception
{
    public int RetCode { get; }
    public string RetMsg { get; }

    public BybitApiException(int retCode, string retMsg)
        : base($"Bybit retCode={retCode}: {retMsg}")
    {
        RetCode = retCode;
        RetMsg = retMsg;
    }
}
