namespace DataService.API.Jobs;

public sealed record MarketWatcherLiveRowSnapshot(
    string Exchange,
    string Symbol,
    string? RealtimeSymbol,
    decimal LastPrice,
    long LastPriceTimestampMs,
    long UpdatedAtMs,
    string[] Frames);

public sealed record MarketWatcherLiveRowsPage(
    IReadOnlyList<MarketWatcherLiveRowSnapshot> Items,
    int Total,
    int Limit,
    int Offset);

public sealed record MarketWatcherStatusSnapshot(
    bool DesiredEnabled,
    bool EffectiveEnabled,
    string Status,
    string? Message,
    long? StartedAtMs,
    long? LastHeartbeatAtMs,
    long? LastFlushAtMs,
    long? LastTickAtMs,
    int TrackedSymbols,
    int LiveRows,
    long? AverageLagMs,
    long? MaxLagMs,
    long TicksInLastWindow,
    int LastFlushRows,
    string[] Exchanges,
    string[] Timeframes,
    string? LastError,
    long? LastErrorAtMs);

public sealed record MarketWatcherLogEntry(
    long Id,
    string Ts,
    string Level,
    string Event,
    string Message,
    IReadOnlyDictionary<string, object?>? Fields);

public sealed class MarketWatcherRuntimeState
{
    private const int MaxLogs = 500;

    private readonly object _gate = new();
    private readonly LinkedList<MarketWatcherLogEntry> _logs = new();
    private long _nextLogId;
    private bool _desiredInitialized;
    private bool _desiredEnabled;
    private bool _effectiveEnabled;
    private string _status = "stopped";
    private string? _message;
    private long? _startedAtMs;
    private long? _lastHeartbeatAtMs;
    private long? _lastFlushAtMs;
    private long? _lastTickAtMs;
    private int _trackedSymbols;
    private int _liveRows;
    private readonly Dictionary<string, MarketWatcherLiveRowSnapshot> _rows = new(StringComparer.OrdinalIgnoreCase);
    private long _ticksInLastWindow;
    private int _lastFlushRows;
    private string[] _exchanges = [];
    private string[] _timeframes = [];
    private string? _lastError;
    private long? _lastErrorAtMs;
    private bool _reloadRequested;

    public bool DesiredEnabled
    {
        get
        {
            lock (_gate) return _desiredEnabled;
        }
    }

    public void InitializeDesiredEnabled(bool enabled)
    {
        lock (_gate)
        {
            if (_desiredInitialized) return;
            _desiredEnabled = enabled;
            _desiredInitialized = true;
        }
    }

    public void SetDesiredEnabled(bool enabled, string source)
    {
        var changed = false;
        lock (_gate)
        {
            if (!_desiredInitialized || _desiredEnabled != enabled)
            {
                _desiredEnabled = enabled;
                _desiredInitialized = true;
                changed = true;
            }
        }

        if (changed)
        {
            AppendLog(
                "info",
                "control.set_enabled",
                enabled ? "Market watcher enabled" : "Market watcher disabled",
                new Dictionary<string, object?>
                {
                    ["source"] = source,
                    ["enabled"] = enabled,
                });
        }
    }

    public void SetConfigured(string[] exchanges, string[] timeframes)
    {
        lock (_gate)
        {
            _exchanges = exchanges;
            _timeframes = timeframes;
        }
    }

    /// <summary>Signal the running watcher loop to re-discover its universe and
    /// re-subscribe — e.g. after the currency-pairs center changed. Consumed
    /// once by the loop via <see cref="ConsumeReloadRequest"/>.</summary>
    public void RequestReload()
    {
        lock (_gate) { _reloadRequested = true; }
    }

    public bool ConsumeReloadRequest()
    {
        lock (_gate)
        {
            if (!_reloadRequested) return false;
            _reloadRequested = false;
            return true;
        }
    }

    public void MarkStarting(string message)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        lock (_gate)
        {
            _effectiveEnabled = true;
            _status = "starting";
            _message = message;
            _startedAtMs = now;
            _lastHeartbeatAtMs = now;
            _lastFlushAtMs = null;
            _lastTickAtMs = null;
            _trackedSymbols = 0;
            _liveRows = 0;
            _rows.Clear();
            _ticksInLastWindow = 0;
            _lastFlushRows = 0;
            _lastError = null;
            _lastErrorAtMs = null;
        }
    }

    public void MarkRunning(
        string message,
        int trackedSymbols,
        int liveRows,
        long ticksInLastWindow,
        int lastFlushRows,
        long? lastTickAtMs)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        lock (_gate)
        {
            _effectiveEnabled = true;
            _status = "running";
            _message = message;
            _lastHeartbeatAtMs = now;
            _lastFlushAtMs = now;
            _lastTickAtMs = lastTickAtMs ?? _lastTickAtMs;
            _trackedSymbols = trackedSymbols;
            _liveRows = _rows.Count > 0 ? _rows.Count : liveRows;
            _ticksInLastWindow = ticksInLastWindow;
            _lastFlushRows = lastFlushRows;
        }
    }

    public void UpsertLiveRow(MarketWatcherLiveRowSnapshot row)
    {
        var key = BuildRowKey(row.Exchange, row.Symbol);
        lock (_gate)
        {
            _rows[key] = row;
            _liveRows = _rows.Count;
            _lastTickAtMs = row.LastPriceTimestampMs;
        }
    }

    /// <summary>
    /// Freshness heartbeat: forward-stamp the live-row timestamp for symbols on
    /// exchanges whose feed is currently alive. For a bid/ask mid, no new tick
    /// means the price has not changed, so the last observed price is still the
    /// current price — this keeps the reported lag bounded to the heartbeat
    /// interval for idle symbols without altering the price, candle aggregation
    /// or DB state. Exchanges absent from <paramref name="aliveExchanges"/>
    /// (stale/dead feed) are intentionally left to accrue lag so a broken feed
    /// remains visible in the status snapshot.
    /// </summary>
    public void RefreshLiveRowFreshness(IReadOnlySet<string> aliveExchanges, long nowMs)
    {
        lock (_gate)
        {
            if (_rows.Count == 0)
            {
                return;
            }

            foreach (var key in _rows.Keys.ToArray())
            {
                var row = _rows[key];
                if (!aliveExchanges.Contains(row.Exchange) || row.LastPriceTimestampMs >= nowMs)
                {
                    continue;
                }

                _rows[key] = row with { LastPriceTimestampMs = nowMs, UpdatedAtMs = nowMs };
            }
        }
    }

    public void RemoveMissingLiveRows(IReadOnlyCollection<(string Exchange, string Symbol)> trackedSymbols)
    {
        lock (_gate)
        {
            if (trackedSymbols.Count == 0)
            {
                _rows.Clear();
                _liveRows = 0;
                return;
            }

            var allowed = trackedSymbols
                .Select(item => BuildRowKey(item.Exchange, item.Symbol))
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            var staleKeys = _rows.Keys
                .Where(key => !allowed.Contains(key))
                .ToArray();

            foreach (var key in staleKeys)
            {
                _rows.Remove(key);
            }

            _liveRows = _rows.Count;
        }
    }

    public MarketWatcherLiveRowsPage ReadLiveRows(
        string? exchange,
        string? search,
        int limit,
        int offset)
    {
        var safeLimit = Math.Clamp(limit, 1, 500);
        var safeOffset = Math.Max(offset, 0);
        var normalizedExchange = string.IsNullOrWhiteSpace(exchange)
            ? null
            : exchange.Trim().ToLowerInvariant();
        var normalizedSearch = string.IsNullOrWhiteSpace(search)
            ? null
            : search.Trim();

        lock (_gate)
        {
            var filtered = _rows.Values
                .Where(row => normalizedExchange is null
                    || string.Equals(row.Exchange, normalizedExchange, StringComparison.OrdinalIgnoreCase))
                .Where(row => normalizedSearch is null
                    || row.Symbol.Contains(normalizedSearch, StringComparison.OrdinalIgnoreCase)
                    || row.Exchange.Contains(normalizedSearch, StringComparison.OrdinalIgnoreCase)
                    || (!string.IsNullOrWhiteSpace(row.RealtimeSymbol)
                        && row.RealtimeSymbol.Contains(normalizedSearch, StringComparison.OrdinalIgnoreCase)))
                .OrderBy(row => row.Symbol, StringComparer.OrdinalIgnoreCase)
                .ThenBy(row => row.Exchange, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            return new MarketWatcherLiveRowsPage(
                Items: filtered.Skip(safeOffset).Take(safeLimit).ToArray(),
                Total: filtered.Length,
                Limit: safeLimit,
                Offset: safeOffset);
        }
    }

    public void MarkDegraded(string message, string? error = null)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        lock (_gate)
        {
            _effectiveEnabled = true;
            _status = "degraded";
            _message = message;
            _lastHeartbeatAtMs = now;
            if (!string.IsNullOrWhiteSpace(error))
            {
                _lastError = error;
                _lastErrorAtMs = now;
            }
        }
    }

    public void MarkError(string message)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        lock (_gate)
        {
            _effectiveEnabled = false;
            _status = "error";
            _message = message;
            _lastHeartbeatAtMs = now;
            _lastError = message;
            _lastErrorAtMs = now;
        }
    }

    public void MarkStopped(string message)
    {
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        lock (_gate)
        {
            _effectiveEnabled = false;
            _status = "stopped";
            _message = message;
            _lastHeartbeatAtMs = now;
            _ticksInLastWindow = 0;
            _lastFlushRows = 0;
        }
    }

    public MarketWatcherStatusSnapshot GetSnapshot()
    {
        lock (_gate)
        {
            var (averageLagMs, maxLagMs) = ComputeLagSnapshotUnsafe();
            return new MarketWatcherStatusSnapshot(
                DesiredEnabled: _desiredEnabled,
                EffectiveEnabled: _effectiveEnabled,
                Status: _status,
                Message: _message,
                StartedAtMs: _startedAtMs,
                LastHeartbeatAtMs: _lastHeartbeatAtMs,
                LastFlushAtMs: _lastFlushAtMs,
                LastTickAtMs: _lastTickAtMs,
                TrackedSymbols: _trackedSymbols,
                LiveRows: _liveRows,
                AverageLagMs: averageLagMs,
                MaxLagMs: maxLagMs,
                TicksInLastWindow: _ticksInLastWindow,
                LastFlushRows: _lastFlushRows,
                Exchanges: _exchanges.ToArray(),
                Timeframes: _timeframes.ToArray(),
                LastError: _lastError,
                LastErrorAtMs: _lastErrorAtMs);
        }
    }

    /// <summary>
    /// Distinct dataset symbols MW is currently flushing on a given exchange,
    /// sorted alphabetically. Used by the gateway's /api/v1/market/config so
    /// the user dropdown lists only the universe that is being persisted in
    /// real time (no orphan dropdown entries with empty Postgres tables).
    /// </summary>
    public IReadOnlyList<string> GetTrackedSymbols(string exchange)
    {
        if (string.IsNullOrWhiteSpace(exchange))
        {
            return Array.Empty<string>();
        }

        lock (_gate)
        {
            return _rows.Values
                .Where(row => string.Equals(row.Exchange, exchange, StringComparison.OrdinalIgnoreCase))
                .Select(row => row.Symbol)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(s => s, StringComparer.OrdinalIgnoreCase)
                .ToArray();
        }
    }

    public IReadOnlyList<MarketWatcherLogEntry> ReadLogs(int limit)
    {
        var safeLimit = Math.Clamp(limit, 1, MaxLogs);
        lock (_gate)
        {
            return _logs
                .Take(safeLimit)
                .ToArray();
        }
    }

    public void AppendLog(
        string level,
        string evt,
        string message,
        IReadOnlyDictionary<string, object?>? fields = null)
    {
        lock (_gate)
        {
            var entry = new MarketWatcherLogEntry(
                Id: Interlocked.Increment(ref _nextLogId),
                Ts: DateTimeOffset.UtcNow.ToString("O"),
                Level: level,
                Event: evt,
                Message: message,
                Fields: fields is null ? null : new Dictionary<string, object?>(fields));
            _logs.AddFirst(entry);
            while (_logs.Count > MaxLogs)
            {
                _logs.RemoveLast();
            }
        }
    }

    private static string BuildRowKey(string exchange, string symbol) => $"{exchange}:{symbol}";

    private (long? AverageLagMs, long? MaxLagMs) ComputeLagSnapshotUnsafe()
    {
        if (_rows.Count == 0)
        {
            return (null, null);
        }

        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        long totalLag = 0;
        long maxLag = 0;
        foreach (var row in _rows.Values)
        {
            var lag = Math.Max(0, now - row.LastPriceTimestampMs);
            totalLag += lag;
            if (lag > maxLag)
            {
                maxLag = lag;
            }
        }

        return (Math.Round((double)totalLag / _rows.Count) is var avg ? (long)avg : null, maxLag);
    }
}