namespace DataService.API.Markets;

public sealed class KrakenRateLimiter
{
    private readonly SemaphoreSlim _concurrencyGate;
    private readonly object _lock = new();
    private DateTimeOffset _nextAllowedAtUtc = DateTimeOffset.MinValue;

    public KrakenRateLimiter(int maxConcurrentRequests = 1, TimeSpan? minSpacing = null)
    {
        if (maxConcurrentRequests <= 0) maxConcurrentRequests = 1;
        _concurrencyGate = new SemaphoreSlim(maxConcurrentRequests, maxConcurrentRequests);
        MinSpacing = minSpacing ?? TimeSpan.FromMilliseconds(1500);
    }

    public TimeSpan MinSpacing { get; }

    public async Task<IDisposable> AcquireAsync(CancellationToken ct = default)
    {
        await _concurrencyGate.WaitAsync(ct);
        try
        {
            var delay = ReserveDelay(DateTimeOffset.UtcNow);
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

    private TimeSpan ReserveDelay(DateTimeOffset nowUtc)
    {
        lock (_lock)
        {
            var next = _nextAllowedAtUtc > nowUtc ? _nextAllowedAtUtc : nowUtc;
            var delay = next - nowUtc;
            _nextAllowedAtUtc = next.Add(MinSpacing);
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