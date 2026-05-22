using System.Collections.Concurrent;
using System.Text.Json;
using Binance.Net.Clients;
using Bybit.Net.Clients;
using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Markets;
using DataService.API.Settings;
using Kraken.Net.Clients;
using Microsoft.Extensions.Options;

namespace DataService.API.Jobs;

public sealed class MarketWatcherService : BackgroundService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly MarketWatchRepository _repo;
    private readonly MarketDataClientFactory _marketDataClientFactory;
    private readonly IOptions<DataServiceSettings> _options;
    private readonly MarketWatcherRuntimeState _state;
    private readonly ILogger<MarketWatcherService> _log;

    public MarketWatcherService(
        MarketWatchRepository repo,
        MarketDataClientFactory marketDataClientFactory,
        IOptions<DataServiceSettings> options,
        MarketWatcherRuntimeState state,
        ILogger<MarketWatcherService> log)
    {
        _repo = repo;
        _marketDataClientFactory = marketDataClientFactory;
        _options = options;
        _state = state;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _state.InitializeDesiredEnabled(_options.Value.MarketWatch.Enabled);
        _state.AppendLog("info", "service.start", "Market watcher service started");

        try
        {
            await _repo.EnsureSchemaAsync(stoppingToken);
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            return;
        }
        catch (Exception ex)
        {
            var message = $"Failed to ensure market watcher schema: {ex.Message}";
            _state.MarkError(message);
            _state.AppendLog("error", "schema.ensure_failed", message);
            _log.LogError(ex, "{Message}", message);
        }

        while (!stoppingToken.IsCancellationRequested)
        {
            var settings = _options.Value.MarketWatch;
            _state.SetConfigured(NormalizeExchanges(settings.Exchanges).ToArray(), NormalizeTimeframes(settings.Timeframes).Keys.ToArray());

            if (!_state.DesiredEnabled)
            {
                _state.MarkStopped("Disabled");
                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
                }
                catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
                {
                    break;
                }
                continue;
            }

            try
            {
                await RunWatcherLoopAsync(settings, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                var message = $"Market watcher loop failed: {ex.Message}";
                _state.MarkError(message);
                _state.AppendLog("error", "loop.failed", message, new Dictionary<string, object?>
                {
                    ["exception"] = ex.GetType().Name,
                });
                _log.LogError(ex, "{Message}", message);

                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
                }
                catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
                {
                    break;
                }
            }
        }

        _state.MarkStopped("Service stopped");
        _state.AppendLog("info", "service.stop", "Market watcher service stopped");
    }

    private async Task RunWatcherLoopAsync(
        MarketWatchSettings settings,
        CancellationToken stoppingToken)
    {
        var exchanges = NormalizeExchanges(settings.Exchanges);
        var timeframes = NormalizeTimeframes(settings.Timeframes);

        if (exchanges.Count == 0)
        {
            throw new InvalidOperationException("Market watcher has no enabled exchanges");
        }

        if (timeframes.Count == 0)
        {
            throw new InvalidOperationException("Market watcher has no enabled timeframes");
        }

        _state.MarkStarting($"Discovering symbols for {string.Join(", ", exchanges)}");
        _state.AppendLog("info", "discover.start", "Discovering market watcher universe", new Dictionary<string, object?>
        {
            ["exchanges"] = exchanges.ToArray(),
            ["timeframes"] = timeframes.Keys.ToArray(),
        });

        var universe = await DiscoverUniverseAsync(exchanges, stoppingToken);
        var totalSymbols = universe.Values.Sum(items => items.Count);
        if (totalSymbols == 0)
        {
            throw new InvalidOperationException("Market watcher did not discover any tradable symbols");
        }

        var realtimeSymbols = universe
            .SelectMany(exchangeEntry => exchangeEntry.Value.Select(symbol => new
            {
                Key = $"{exchangeEntry.Key}:{symbol.Symbol}",
                RealtimeSymbol = symbol.RealtimeSymbol,
            }))
            .ToDictionary(item => item.Key, item => item.RealtimeSymbol, StringComparer.OrdinalIgnoreCase);

        var state = new ConcurrentDictionary<string, SymbolLiveState>(StringComparer.OrdinalIgnoreCase);
        long tickCount = 0;
        long lastTickAtMs = 0;

        void OnPrice(string exchange, string symbol, decimal price, DateTimeOffset timestampUtc)
        {
            var key = $"{exchange}:{symbol}";
            var realtimeSymbol = realtimeSymbols.TryGetValue(key, out var rt) ? rt : symbol;
            var symbolState = state.GetOrAdd(key, _ => new SymbolLiveState(exchange, symbol, realtimeSymbol));
            symbolState.Apply(price, timestampUtc, timeframes);
            Interlocked.Increment(ref tickCount);
            Interlocked.Exchange(ref lastTickAtMs, timestampUtc.ToUnixTimeMilliseconds());
        }

        await using var subscriptions = await StartSubscriptionsAsync(universe, settings, OnPrice, stoppingToken);

        var flushEvery = TimeSpan.FromMilliseconds(Math.Max(250, settings.FlushIntervalMs));
        var reportEvery = TimeSpan.FromSeconds(Math.Max(5, settings.ProgressIntervalSeconds));
        var nextReportAt = DateTimeOffset.UtcNow.Add(reportEvery);

        _state.MarkRunning(
            $"Watching {totalSymbols} symbols across {string.Join(", ", universe.Keys)}",
            totalSymbols,
            0,
            0,
            0,
            null);
        _state.AppendLog("success", "watch.start", "Market watcher subscriptions active", new Dictionary<string, object?>
        {
            ["totalSymbols"] = totalSymbols,
            ["exchanges"] = universe.Keys.ToArray(),
        });

        while (!stoppingToken.IsCancellationRequested)
        {
            if (!_state.DesiredEnabled)
            {
                _state.MarkStopped("Disabled by operator");
                _state.AppendLog("info", "watch.stop_requested", "Market watcher disabled by operator");
                return;
            }

            await Task.Delay(flushEvery, stoppingToken);

            var pending = CollectPendingSnapshots(state);
            if (pending.Count > 0)
            {
                await _repo.UpsertSnapshotsAsync(pending.Select(item => item.Snapshot).ToArray(), stoppingToken);
                foreach (var item in pending)
                {
                    item.State.MarkPersisted(item.Version);
                }
            }

            var now = DateTimeOffset.UtcNow;
            if (now >= nextReportAt)
            {
                var ticksPerWindow = Interlocked.Exchange(ref tickCount, 0);
                var latestTickAtMs = Interlocked.Read(ref lastTickAtMs);
                _state.MarkRunning(
                    $"Watching {totalSymbols} symbols",
                    totalSymbols,
                    state.Count,
                    ticksPerWindow,
                    pending.Count,
                    latestTickAtMs > 0 ? latestTickAtMs : null);
                _state.AppendLog("info", "watch.summary", "Market watcher heartbeat", new Dictionary<string, object?>
                {
                    ["trackedSymbols"] = totalSymbols,
                    ["liveRows"] = state.Count,
                    ["ticksInWindow"] = ticksPerWindow,
                    ["flushedRows"] = pending.Count,
                });
                nextReportAt = now.Add(reportEvery);
            }
        }
    }

    private async Task<Dictionary<string, IReadOnlyList<MarketWatchSymbol>>> DiscoverUniverseAsync(
        IReadOnlyCollection<string> exchanges,
        CancellationToken ct)
    {
        var result = new Dictionary<string, IReadOnlyList<MarketWatchSymbol>>(StringComparer.OrdinalIgnoreCase);
        var errors = new List<string>();

        foreach (var exchange in exchanges)
        {
            var client = _marketDataClientFactory.GetRequiredClient(exchange);
            try
            {
                var symbols = await client.FetchMarketWatchSymbolsAsync(ct);
                if (symbols.Count == 0)
                {
                    _state.AppendLog("warning", "discover.empty", $"No symbols discovered for {exchange}");
                    continue;
                }

                result[exchange] = symbols;
                _state.AppendLog("info", "discover.exchange", $"Discovered {symbols.Count} symbols for {exchange}", new Dictionary<string, object?>
                {
                    ["exchange"] = exchange,
                    ["symbols"] = symbols.Count,
                });
            }
            catch (Exception ex)
            {
                var fallback = await TryLoadFallbackSymbolsAsync(exchange, ct);
                if (fallback.Count > 0)
                {
                    result[exchange] = fallback;
                    _state.MarkDegraded($"{exchange} discovery failed; using persisted watcher state", ex.Message);
                    _state.AppendLog("warning", "discover.fallback", $"Using persisted symbol list for {exchange}", new Dictionary<string, object?>
                    {
                        ["exchange"] = exchange,
                        ["symbols"] = fallback.Count,
                        ["error"] = ex.Message,
                    });
                    _log.LogWarning(ex, "Market watcher discovery failed for {Exchange}; using {Count} persisted symbols", exchange, fallback.Count);
                    continue;
                }

                errors.Add($"{exchange}: {ex.Message}");
                _state.AppendLog("error", "discover.failed", $"Discovery failed for {exchange}", new Dictionary<string, object?>
                {
                    ["exchange"] = exchange,
                    ["error"] = ex.Message,
                });
                _log.LogError(ex, "Market watcher discovery failed for {Exchange}", exchange);
            }
        }

        if (errors.Count > 0 && result.Count > 0)
        {
            _state.MarkDegraded($"Partial discovery failure: {string.Join("; ", errors)}", string.Join("; ", errors));
        }

        return result;
    }

    private async Task<IReadOnlyList<MarketWatchSymbol>> TryLoadFallbackSymbolsAsync(string exchange, CancellationToken ct)
    {
        if (string.Equals(exchange, KrakenApiClient.ExchangeName, StringComparison.OrdinalIgnoreCase)
            || string.Equals(exchange, BinanceApiClient.ExchangeName, StringComparison.OrdinalIgnoreCase)
            || string.Equals(exchange, "bybit", StringComparison.OrdinalIgnoreCase))
        {
            return await _repo.ListKnownSymbolsAsync(exchange, ct);
        }

        return Array.Empty<MarketWatchSymbol>();
    }

    private async Task<IAsyncDisposable> StartSubscriptionsAsync(
        IReadOnlyDictionary<string, IReadOnlyList<MarketWatchSymbol>> universe,
        MarketWatchSettings settings,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var handles = new List<IAsyncDisposable>();
        var failures = new List<string>();

        async Task TryStartAsync(
            string exchange,
            Func<Task<IAsyncDisposable>> factory)
        {
            try
            {
                handles.Add(await factory());
                _state.AppendLog("success", "subscribe.exchange", $"Subscriptions active for {exchange}");
            }
            catch (Exception ex)
            {
                failures.Add($"{exchange}: {ex.Message}");
                _state.AppendLog("error", "subscribe.failed", $"Subscription failed for {exchange}", new Dictionary<string, object?>
                {
                    ["exchange"] = exchange,
                    ["error"] = ex.Message,
                });
                _log.LogError(ex, "Market watcher subscription failed for {Exchange}", exchange);
            }
        }

        if (universe.TryGetValue("binance", out var binanceSymbols) && binanceSymbols.Count > 0)
        {
            await TryStartAsync("binance", () => StartBinanceAsync(onPrice, ct));
        }

        if (universe.TryGetValue("bybit", out var bybitSymbols) && bybitSymbols.Count > 0)
        {
            await TryStartAsync("bybit", () => StartBybitAsync(bybitSymbols, settings, onPrice, ct));
        }

        if (universe.TryGetValue("kraken", out var krakenSymbols) && krakenSymbols.Count > 0)
        {
            await TryStartAsync("kraken", () => StartKrakenAsync(krakenSymbols, settings, onPrice, ct));
        }

        if (handles.Count == 0)
        {
            throw new InvalidOperationException($"No market watcher subscriptions started: {string.Join("; ", failures)}");
        }

        if (failures.Count > 0)
        {
            _state.MarkDegraded($"Some subscriptions failed: {string.Join("; ", failures)}", string.Join("; ", failures));
        }

        return new CompositeAsyncDisposable(handles);
    }

    private static async Task<IAsyncDisposable> StartBinanceAsync(
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var socketClient = new BinanceSocketClient();
        var subscriptions = new List<Func<ValueTask>>();

        var subscription = await socketClient.UsdFuturesApi.ExchangeData.SubscribeToAllTickerUpdatesAsync(update =>
        {
            var now = DateTimeOffset.UtcNow;
            foreach (var ticker in update.Data)
            {
                if (string.IsNullOrWhiteSpace(ticker.Symbol) || ticker.LastPrice <= 0) continue;
                onPrice("binance", ticker.Symbol, ticker.LastPrice, now);
            }
        }, ct);

        if (!subscription.Success)
        {
            socketClient.Dispose();
            throw new InvalidOperationException($"Binance ticker subscription failed: {subscription.Error}");
        }

        subscriptions.Add(async () =>
        {
            await socketClient.UnsubscribeAsync(subscription.Data);
            socketClient.Dispose();
        });

        return new CompositeAsyncDisposable(subscriptions.Select(action => new AsyncDisposeAction(action)).ToArray());
    }

    private static async Task<IAsyncDisposable> StartBybitAsync(
        IReadOnlyList<MarketWatchSymbol> symbols,
        MarketWatchSettings settings,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var socketClient = new BybitSocketClient();
        var disposers = new List<Func<ValueTask>>();

        foreach (var chunk in symbols
            .Select(item => item.RealtimeSymbol ?? item.Symbol)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Chunk(Math.Max(1, settings.BybitSymbolsPerSubscription)))
        {
            var subscription = await socketClient.V5LinearApi.SubscribeToTickerUpdatesAsync(chunk, update =>
            {
                var price = update.Data.LastPrice;
                if (!price.HasValue || string.IsNullOrWhiteSpace(update.Data.Symbol) || price.Value <= 0) return;
                onPrice("bybit", update.Data.Symbol, price.Value, DateTimeOffset.UtcNow);
            }, ct);

            if (!subscription.Success)
            {
                socketClient.Dispose();
                throw new InvalidOperationException($"Bybit ticker subscription failed: {subscription.Error}");
            }

            disposers.Add(() => new ValueTask(socketClient.UnsubscribeAsync(subscription.Data)));
        }

        disposers.Add(() =>
        {
            socketClient.Dispose();
            return ValueTask.CompletedTask;
        });

        return new CompositeAsyncDisposable(disposers.Select(action => new AsyncDisposeAction(action)).ToArray());
    }

    private static async Task<IAsyncDisposable> StartKrakenAsync(
        IReadOnlyList<MarketWatchSymbol> symbols,
        MarketWatchSettings settings,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var socketClient = new KrakenSocketClient();
        var disposers = new List<Func<ValueTask>>();
        var symbolMap = symbols
            .Where(item => !string.IsNullOrWhiteSpace(item.RealtimeSymbol))
            .ToDictionary(item => item.RealtimeSymbol!, item => item.Symbol, StringComparer.OrdinalIgnoreCase);

        foreach (var chunk in symbolMap.Keys.Chunk(Math.Max(1, settings.KrakenSymbolsPerSubscription)))
        {
            var subscription = await socketClient.SpotApi.SubscribeToTickerUpdatesAsync(chunk, update =>
            {
                if (string.IsNullOrWhiteSpace(update.Data.Symbol) || update.Data.LastPrice <= 0) return;

                var normalized = symbolMap.TryGetValue(update.Data.Symbol, out var symbol)
                    ? symbol
                    : update.Data.Symbol.Replace("/", string.Empty, StringComparison.Ordinal);
                onPrice("kraken", normalized, update.Data.LastPrice, DateTimeOffset.UtcNow);
            }, snapshot: true, ct: ct);

            if (!subscription.Success)
            {
                socketClient.Dispose();
                throw new InvalidOperationException($"Kraken ticker subscription failed: {subscription.Error}");
            }

            disposers.Add(() => new ValueTask(socketClient.UnsubscribeAsync(subscription.Data)));
        }

        disposers.Add(() =>
        {
            socketClient.Dispose();
            return ValueTask.CompletedTask;
        });

        return new CompositeAsyncDisposable(disposers.Select(action => new AsyncDisposeAction(action)).ToArray());
    }

    private static List<PendingSnapshot> CollectPendingSnapshots(
        ConcurrentDictionary<string, SymbolLiveState> state)
    {
        var snapshots = new List<PendingSnapshot>(state.Count);
        foreach (var item in state.Values)
        {
            var snapshot = item.TryCreateSnapshot();
            if (snapshot is not null)
            {
                snapshots.Add(snapshot);
            }
        }

        return snapshots;
    }

    private static Dictionary<string, long> NormalizeTimeframes(IEnumerable<string>? configured)
    {
        var requested = configured?
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Select(item => item.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var effective = requested is { Length: > 0 }
            ? requested
            : DatasetConstants.Timeframes.Keys.ToArray();

        var result = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
        foreach (var timeframe in effective)
        {
            if (DatasetConstants.Timeframes.TryGetValue(timeframe, out var spec))
            {
                result[timeframe] = spec.StepMs;
            }
        }

        return result;
    }

    private static HashSet<string> NormalizeExchanges(IEnumerable<string>? configured)
    {
        var requested = configured?
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Select(item => item.Trim().ToLowerInvariant())
            .Where(MarketDataClientFactory.IsSupportedExchange)
            .ToArray();

        return requested is { Length: > 0 }
            ? new HashSet<string>(requested, StringComparer.OrdinalIgnoreCase)
            : new HashSet<string>(MarketDataClientFactory.SupportedExchanges, StringComparer.OrdinalIgnoreCase);
    }

    private sealed record PendingSnapshot(
        MarketWatchSymbolSnapshot Snapshot,
        SymbolLiveState State,
        long Version);

    private sealed class SymbolLiveState
    {
        private readonly object _gate = new();
        private readonly Dictionary<string, MarketWatchCandleSnapshot> _candles = new(StringComparer.OrdinalIgnoreCase);
        private readonly string _exchange;
        private readonly string _symbol;
        private readonly string? _realtimeSymbol;
        private long _version;
        private bool _dirty;
        private decimal _lastPrice;
        private DateTimeOffset _lastPriceTimestampUtc;

        public SymbolLiveState(string exchange, string symbol, string? realtimeSymbol)
        {
            _exchange = exchange;
            _symbol = symbol;
            _realtimeSymbol = realtimeSymbol;
        }

        public void Apply(decimal price, DateTimeOffset timestampUtc, IReadOnlyDictionary<string, long> timeframes)
        {
            lock (_gate)
            {
                _lastPrice = price;
                _lastPriceTimestampUtc = timestampUtc;
                var timestampMs = timestampUtc.ToUnixTimeMilliseconds();

                foreach (var timeframe in timeframes)
                {
                    var bucketStartMs = timestampMs - timestampMs % timeframe.Value;
                    if (!_candles.TryGetValue(timeframe.Key, out var candle)
                        || candle.BucketStartMs != bucketStartMs)
                    {
                        _candles[timeframe.Key] = new MarketWatchCandleSnapshot(
                            BucketStartMs: bucketStartMs,
                            Open: price,
                            High: price,
                            Low: price,
                            Close: price,
                            LastUpdateMs: timestampMs);
                        continue;
                    }

                    _candles[timeframe.Key] = candle with
                    {
                        High = Math.Max(candle.High, price),
                        Low = Math.Min(candle.Low, price),
                        Close = price,
                        LastUpdateMs = timestampMs,
                    };
                }

                _dirty = true;
                _version++;
            }
        }

        public PendingSnapshot? TryCreateSnapshot()
        {
            lock (_gate)
            {
                if (!_dirty || _lastPriceTimestampUtc == default)
                {
                    return null;
                }

                var candlesCopy = new Dictionary<string, MarketWatchCandleSnapshot>(_candles, StringComparer.OrdinalIgnoreCase);
                return new PendingSnapshot(
                    new MarketWatchSymbolSnapshot(
                        Exchange: _exchange,
                        Symbol: _symbol,
                        RealtimeSymbol: _realtimeSymbol,
                        LastPrice: _lastPrice,
                        LastPriceTimestampUtc: _lastPriceTimestampUtc,
                        CandlesJson: JsonSerializer.Serialize(candlesCopy, JsonOptions)),
                    this,
                    _version);
            }
        }

        public void MarkPersisted(long persistedVersion)
        {
            lock (_gate)
            {
                if (_version == persistedVersion)
                {
                    _dirty = false;
                }
            }
        }
    }

    private sealed class AsyncDisposeAction : IAsyncDisposable
    {
        private readonly Func<ValueTask> _dispose;

        public AsyncDisposeAction(Func<ValueTask> dispose)
        {
            _dispose = dispose;
        }

        public ValueTask DisposeAsync() => _dispose();
    }

    private sealed class CompositeAsyncDisposable : IAsyncDisposable
    {
        private readonly IReadOnlyList<IAsyncDisposable> _items;

        public CompositeAsyncDisposable(IReadOnlyList<IAsyncDisposable> items)
        {
            _items = items;
        }

        public async ValueTask DisposeAsync()
        {
            foreach (var item in _items.Reverse())
            {
                try { await item.DisposeAsync(); } catch { }
            }
        }
    }
}