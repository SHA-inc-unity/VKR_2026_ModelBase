namespace DataService.API.Markets;

public sealed class BinanceRateLimiter
{
    private readonly SemaphoreSlim _concurrencyGate;
    private readonly object _lock = new();
    private DateTimeOffset _nextAllowedAtUtc = DateTimeOffset.MinValue;

    public BinanceRateLimiter(int maxConcurrentRequests = 3, TimeSpan? unitSpacing = null)
    {
        if (maxConcurrentRequests <= 0) maxConcurrentRequests = 1;
        _concurrencyGate = new SemaphoreSlim(maxConcurrentRequests, maxConcurrentRequests);
        UnitSpacing = unitSpacing ?? TimeSpan.FromMilliseconds(100);
    }

    public TimeSpan UnitSpacing { get; }

    public async Task<IDisposable> AcquireAsync(int units = 1, CancellationToken ct = default)
    {
        units = Math.Max(1, units);
        await _concurrencyGate.WaitAsync(ct);
        try
        {
            var delay = ReserveDelay(DateTimeOffset.UtcNow, units);
            if (delay > TimeSpan.Zero)
            {
                await Task.Delay(delay, ct);
            }

            return new Releaser(_concurrencyGate);
        }
        catch
        {
            _concurrencyGate.Release();
            throw;
        }
    }

    public void Penalize(TimeSpan delay)
    {
        if (delay <= TimeSpan.Zero) return;
        lock (_lock)
        {
            var candidate = DateTimeOffset.UtcNow.Add(delay);
            if (candidate > _nextAllowedAtUtc)
            {
                _nextAllowedAtUtc = candidate;
            }
        }
    }

    private TimeSpan ReserveDelay(DateTimeOffset nowUtc, int units)
    {
        lock (_lock)
        {
            var next = _nextAllowedAtUtc > nowUtc ? _nextAllowedAtUtc : nowUtc;
            var delay = next - nowUtc;
            _nextAllowedAtUtc = next.Add(TimeSpan.FromTicks(UnitSpacing.Ticks * units));
            return delay;
        }
    }

    private sealed class Releaser : IDisposable
    {
        private readonly SemaphoreSlim _gate;
        private int _disposed;

        public Releaser(SemaphoreSlim gate)
        {
            _gate = gate;
        }

        public void Dispose()
        {
            if (Interlocked.Exchange(ref _disposed, 1) == 0)
            {
                _gate.Release();
            }
        }
    }
}