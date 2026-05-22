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

public sealed class MarketWatchJobHandler : IDatasetJobHandler
{
    private const string MarketWatchTargetTable = "market_watch_live";
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly MarketWatchRepository _repo;
    private readonly MarketDataClientFactory _marketDataClientFactory;
    private readonly IOptions<DataServiceSettings> _options;
    private readonly ILogger<MarketWatchJobHandler> _log;

    public MarketWatchJobHandler(
        MarketWatchRepository repo,
        MarketDataClientFactory marketDataClientFactory,
        IOptions<DataServiceSettings> options,
        ILogger<MarketWatchJobHandler> log)
    {
        _repo = repo;
        _marketDataClientFactory = marketDataClientFactory;
        _options = options;
        _log = log;
    }

    public string Type => DatasetJobType.MarketWatch;

    public async Task ExecuteAsync(JobContext ctx)
    {
        var settings = _options.Value.MarketWatch;
        if (!settings.Enabled)
        {
            throw new OperationCanceledException("Market watch is disabled by configuration", ctx.CancellationToken);
        }

        var exchanges = NormalizeExchanges(settings.Exchanges);
        var timeframes = NormalizeTimeframes(settings.Timeframes);
        if (exchanges.Count == 0)
        {
            throw new InvalidOperationException("Market watch has no enabled exchanges");
        }

        if (timeframes.Count == 0)
        {
            throw new InvalidOperationException("Market watch has no enabled timeframes");
        }

        await _repo.EnsureSchemaAsync(ctx.CancellationToken);
        await ctx.ReportAsync(
            stage: "discovering",
            progress: 5,
            detail: $"discovering symbols for {string.Join(", ", exchanges)}",
            total: exchanges.Count,
            completed: 0);

        var universe = await DiscoverUniverseAsync(exchanges, ctx.CancellationToken);
        var totalSymbols = universe.Values.Sum(items => items.Count);
        if (totalSymbols == 0)
        {
            throw new InvalidOperationException("Market watch did not discover any tradable symbols");
        }

        var state = new ConcurrentDictionary<string, SymbolLiveState>(StringComparer.OrdinalIgnoreCase);
        long tickCount = 0;

        void OnPrice(string exchange, string symbol, decimal price, DateTimeOffset timestampUtc)
        {
            var key = $"{exchange}:{symbol}";
            var symbolState = state.GetOrAdd(key, _ => new SymbolLiveState(exchange, symbol));
            symbolState.Apply(price, timestampUtc, timeframes);
            Interlocked.Increment(ref tickCount);
        }

        await ctx.ReportAsync(
            stage: "subscribing",
            progress: 15,
            detail: $"opening websocket subscriptions for {totalSymbols} symbols",
            total: totalSymbols,
            completed: 0);

        await using var subscriptions = await StartSubscriptionsAsync(universe, settings, OnPrice, ctx.CancellationToken);

        var flushEvery = TimeSpan.FromMilliseconds(Math.Max(250, settings.FlushIntervalMs));
        var reportEvery = TimeSpan.FromSeconds(Math.Max(5, settings.ProgressIntervalSeconds));
        var nextReportAt = DateTimeOffset.UtcNow.Add(reportEvery);

        await ctx.ReportAsync(
            stage: "watching",
            progress: 100,
            detail: $"watching {totalSymbols} symbols across {exchanges.Count} exchanges",
            total: totalSymbols,
            completed: totalSymbols);

        while (true)
        {
            ctx.CancellationToken.ThrowIfCancellationRequested();
            if (await ctx.IsCancelRequestedAsync())
            {
                throw new OperationCanceledException(ctx.CancellationToken);
            }

            await Task.Delay(flushEvery, ctx.CancellationToken);

            var pending = CollectPendingSnapshots(state);
            if (pending.Count > 0)
            {
                await _repo.UpsertSnapshotsAsync(pending.Select(item => item.Snapshot).ToArray(), ctx.CancellationToken);
                foreach (var item in pending)
                {
                    item.State.MarkPersisted(item.Version);
                }
            }

            var now = DateTimeOffset.UtcNow;
            if (now >= nextReportAt)
            {
                var ticksPerWindow = Interlocked.Exchange(ref tickCount, 0);
                await ctx.ReportAsync(
                    stage: "watching",
                    progress: 100,
                    detail: $"symbols={totalSymbols}; live_rows={state.Count}; ticks={ticksPerWindow}; flushed={pending.Count}",
                    total: totalSymbols,
                    completed: totalSymbols,
                    skipped: 0,
                    failed: 0);
                nextReportAt = now.Add(reportEvery);
            }
        }
    }

    private async Task<Dictionary<string, IReadOnlyList<MarketWatchSymbol>>> DiscoverUniverseAsync(
        IReadOnlyCollection<string> exchanges,
        CancellationToken ct)
    {
        var result = new Dictionary<string, IReadOnlyList<MarketWatchSymbol>>(StringComparer.OrdinalIgnoreCase);
        foreach (var exchange in exchanges)
        {
            var client = _marketDataClientFactory.GetRequiredClient(exchange);
            var symbols = await client.FetchMarketWatchSymbolsAsync(ct);
            result[exchange] = symbols;
            _log.LogInformation("Market watch discovered {Count} symbols for {Exchange}", symbols.Count, exchange);
        }

        return result;
    }

    private async Task<IAsyncDisposable> StartSubscriptionsAsync(
        IReadOnlyDictionary<string, IReadOnlyList<MarketWatchSymbol>> universe,
        MarketWatchSettings settings,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var handles = new List<IAsyncDisposable>();
        try
        {
            if (universe.TryGetValue("binance", out var binanceSymbols) && binanceSymbols.Count > 0)
            {
                handles.Add(await StartBinanceAsync(onPrice, ct));
            }

            if (universe.TryGetValue("bybit", out var bybitSymbols) && bybitSymbols.Count > 0)
            {
                handles.Add(await StartBybitAsync(bybitSymbols, settings, onPrice, ct));
            }

            if (universe.TryGetValue("kraken", out var krakenSymbols) && krakenSymbols.Count > 0)
            {
                handles.Add(await StartKrakenAsync(krakenSymbols, settings, onPrice, ct));
            }

            return new CompositeAsyncDisposable(handles);
        }
        catch
        {
            foreach (var handle in handles.AsEnumerable().Reverse())
            {
                try { await handle.DisposeAsync(); } catch { }
            }

            throw;
        }
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
        private long _version;
        private bool _dirty;
        private decimal _lastPrice;
        private DateTimeOffset _lastPriceTimestampUtc;

        public SymbolLiveState(string exchange, string symbol)
        {
            _exchange = exchange;
            _symbol = symbol;
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