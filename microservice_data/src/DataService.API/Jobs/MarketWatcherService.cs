using System.Collections.Concurrent;
using System.Text.Json;
using Binance.Net.Clients;
using Bybit.Net.Clients;
using CryptoExchange.Net.Interfaces;
using CryptoExchange.Net.Objects;
using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Markets;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Jobs;

public sealed class MarketWatcherService : BackgroundService
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly TimeSpan DiscoveryDeadline = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan SubscriptionRetryDelay = TimeSpan.FromSeconds(3);
    private const int SubscriptionStartAttempts = 3;

    // Freshness heartbeat: re-stamp live rows this often so idle (non-moving)
    // symbols report sub-second lag instead of "seconds since last book change".
    private const int FreshnessHeartbeatMs = 400;
    // Only re-stamp an exchange's rows while its feed is alive (received any
    // update within this window). A dead feed stops re-stamping → lag grows →
    // a broken feed stays observable instead of being masked.
    private const long FeedAliveWindowMs = 10_000;

    private readonly MarketWatchRepository _repo;
    private readonly DatasetRepository _datasetRepo;
    private readonly CurrencyPairsRepository _pairsRepo;
    private readonly MarketDataClientFactory _marketDataClientFactory;
    private readonly IOptions<DataServiceSettings> _options;
    private readonly MarketWatcherRuntimeState _state;
    private readonly ILogger<MarketWatcherService> _log;

    public MarketWatcherService(
        MarketWatchRepository repo,
        DatasetRepository datasetRepo,
        CurrencyPairsRepository pairsRepo,
        MarketDataClientFactory marketDataClientFactory,
        IOptions<DataServiceSettings> options,
        MarketWatcherRuntimeState state,
        ILogger<MarketWatcherService> log)
    {
        _repo = repo;
        _datasetRepo = datasetRepo;
        _pairsRepo = pairsRepo;
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
            await _pairsRepo.EnsureSchemaAsync(stoppingToken);
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
        // Configured symbols come from the currency-pairs center (active base ×
        // active quote). Fall back to the static settings list only if the
        // center is empty (e.g. before the first seed completes).
        var centerSymbols = await LoadCenterSymbolsAsync(stoppingToken);
        var configuredSymbols = NormalizeSymbols(centerSymbols.Count > 0 ? centerSymbols : settings.Symbols);
        var timeframes = NormalizeTimeframes(settings.Timeframes);

        if (exchanges.Count == 0)
        {
            throw new InvalidOperationException("Market watcher has no enabled exchanges");
        }

        if (configuredSymbols.Count == 0)
        {
            throw new InvalidOperationException("Market watcher has no configured dataset symbols");
        }

        if (timeframes.Count == 0)
        {
            throw new InvalidOperationException("Market watcher has no enabled timeframes");
        }

        _state.MarkStarting($"Discovering configured dataset symbols for {string.Join(", ", exchanges)}");
        _state.AppendLog("info", "discover.start", "Discovering market watcher universe", new Dictionary<string, object?>
        {
            ["exchanges"] = exchanges.ToArray(),
            ["symbols"] = configuredSymbols.ToArray(),
            ["timeframes"] = timeframes.Keys.ToArray(),
        });

        var universe = await DiscoverUniverseAsync(exchanges, configuredSymbols, stoppingToken);
        var totalSymbols = universe.Values.Sum(items => items.Count);
        if (totalSymbols == 0)
        {
            throw new InvalidOperationException("Market watcher did not discover any configured dataset symbols");
        }

        var realtimeSymbols = universe
            .SelectMany(exchangeEntry => exchangeEntry.Value.Select(symbol => new
            {
                Key = $"{exchangeEntry.Key}:{symbol.Symbol}",
                RealtimeSymbol = symbol.RealtimeSymbol,
            }))
            .ToDictionary(item => item.Key, item => item.RealtimeSymbol, StringComparer.OrdinalIgnoreCase);

        var trackedSymbols = universe
            .SelectMany(exchangeEntry => exchangeEntry.Value.Select(symbol => (Exchange: exchangeEntry.Key, Symbol: symbol.Symbol)))
            .ToArray();
        var prunedRows = await _repo.PruneLiveRowsAsync(trackedSymbols, stoppingToken);
        if (prunedRows > 0)
        {
            _state.AppendLog("info", "watch.prune", "Pruned stale market watcher rows", new Dictionary<string, object?>
            {
                ["rows"] = prunedRows,
                ["trackedSymbols"] = trackedSymbols.Length,
            });
        }

        var state = new ConcurrentDictionary<string, SymbolLiveState>(StringComparer.OrdinalIgnoreCase);
        var lastExchangeUpdateMs = new ConcurrentDictionary<string, long>(StringComparer.OrdinalIgnoreCase);
        long tickCount = 0;
        long lastTickAtMs = 0;

        void OnPrice(string exchange, string symbol, decimal price, DateTimeOffset timestampUtc)
        {
            var key = $"{exchange}:{symbol}";
            var realtimeSymbol = realtimeSymbols.TryGetValue(key, out var rt) ? rt : symbol;
            var symbolState = state.GetOrAdd(key, _ => new SymbolLiveState(exchange, symbol, realtimeSymbol));
            var row = symbolState.Apply(price, timestampUtc, timeframes);
            _state.UpsertLiveRow(row);
            Interlocked.Increment(ref tickCount);
            var tickAtMs = timestampUtc.ToUnixTimeMilliseconds();
            Interlocked.Exchange(ref lastTickAtMs, tickAtMs);
            lastExchangeUpdateMs[exchange] = tickAtMs;
        }

        var subscriptionStart = await StartSubscriptionsAsync(universe, settings, OnPrice, stoppingToken);
        await using var subscriptions = subscriptionStart.Handle;

        var activeExchanges = subscriptionStart.ActiveExchanges.Count > 0
            ? subscriptionStart.ActiveExchanges
            : universe.Keys.OrderBy(item => item, StringComparer.OrdinalIgnoreCase).ToArray();
        var activeExchangeSet = new HashSet<string>(activeExchanges, StringComparer.OrdinalIgnoreCase);
        var activeTrackedSymbols = trackedSymbols
            .Where(item => activeExchangeSet.Contains(item.Exchange))
            .ToArray();
        var activeSymbolCount = activeTrackedSymbols.Length;

        if (activeTrackedSymbols.Length != trackedSymbols.Length)
        {
            _state.RemoveMissingLiveRows(activeTrackedSymbols);
            await _repo.PruneLiveRowsAsync(activeTrackedSymbols, stoppingToken);
        }

        var runningMessage = subscriptionStart.Warnings.Count == 0
            ? $"Watching {activeSymbolCount} symbols across {string.Join(", ", activeExchanges)}"
            : $"Watching {activeSymbolCount} symbols across {string.Join(", ", activeExchanges)}; startup degraded: {string.Join("; ", subscriptionStart.Warnings)}";

        if (subscriptionStart.Warnings.Count > 0)
        {
            _state.MarkDegraded(runningMessage, string.Join("; ", subscriptionStart.Warnings));
        }

        var flushEvery = TimeSpan.FromMilliseconds(Math.Max(250, settings.FlushIntervalMs));
        var reportEvery = TimeSpan.FromSeconds(Math.Max(5, settings.ProgressIntervalSeconds));
        var nextReportAt = DateTimeOffset.UtcNow.Add(reportEvery);

        _state.MarkRunning(
            runningMessage,
            activeSymbolCount,
            0,
            0,
            0,
            null);
        _state.AppendLog("success", "watch.start", "Market watcher subscriptions active", new Dictionary<string, object?>
        {
            ["totalSymbols"] = activeSymbolCount,
            ["prunedRows"] = prunedRows,
            ["exchanges"] = activeExchanges.ToArray(),
            ["startupWarnings"] = subscriptionStart.Warnings.ToArray(),
        });

        // Freshness heartbeat runs on its own cadence, independent of the
        // flush/persist loop below, so a candle-rollover persist burst (which
        // now includes ccxt fetches) can never delay it.
        using var heartbeatCts = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        var heartbeatTask = RunFreshnessHeartbeatAsync(lastExchangeUpdateMs, heartbeatCts.Token);

        try
        {
        while (!stoppingToken.IsCancellationRequested)
        {
            if (!_state.DesiredEnabled)
            {
                _state.MarkStopped("Disabled by operator");
                _state.AppendLog("info", "watch.stop_requested", "Market watcher disabled by operator");
                return;
            }

            await Task.Delay(flushEvery, stoppingToken);

            if (_state.ConsumeReloadRequest())
            {
                // Currency-pairs center changed: exit the inner loop so the
                // finally below tears down the heartbeat, the method returns,
                // old subscriptions are disposed (await using), and the outer
                // ExecuteAsync loop re-enters RunWatcherLoopAsync to re-discover
                // the universe and re-subscribe with the new pair list.
                _state.AppendLog("info", "watch.reload", "Reloading watcher universe (currency pairs changed)");
                break;
            }

            var pending = CollectPendingSnapshots(state);
            if (pending.Count > 0)
            {
                await _repo.UpsertSnapshotsAsync(pending.Select(item => item.Snapshot).ToArray(), stoppingToken);
                await PersistClosedCandlesAsync(pending, stoppingToken);
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
                    runningMessage,
                    activeSymbolCount,
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
        finally
        {
            heartbeatCts.Cancel();
            try { await heartbeatTask; } catch { /* shutting down */ }
        }
    }

    private async Task RunFreshnessHeartbeatAsync(
        ConcurrentDictionary<string, long> lastExchangeUpdateMs,
        CancellationToken ct)
    {
        var interval = TimeSpan.FromMilliseconds(FreshnessHeartbeatMs);
        try
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(interval, ct);

                var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                var alive = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var entry in lastExchangeUpdateMs)
                {
                    if (nowMs - entry.Value <= FeedAliveWindowMs)
                    {
                        alive.Add(entry.Key);
                    }
                }

                if (alive.Count > 0)
                {
                    _state.RefreshLiveRowFreshness(alive, nowMs);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // normal shutdown
        }
    }

    private async Task<Dictionary<string, IReadOnlyList<MarketWatchSymbol>>> DiscoverUniverseAsync(
        IReadOnlyCollection<string> exchanges,
        HashSet<string> configuredSymbols,
        CancellationToken ct)
    {
        var result = new Dictionary<string, IReadOnlyList<MarketWatchSymbol>>(StringComparer.OrdinalIgnoreCase);
        var errors = new List<string>();

        foreach (var exchange in exchanges)
        {
            var client = _marketDataClientFactory.GetRequiredClient(exchange);
            try
            {
                var discoveredSymbols = await FetchMarketWatchSymbolsAsync(client, exchange, configuredSymbols, ct);
                var filteredSymbols = FilterConfiguredSymbols(discoveredSymbols, configuredSymbols);
                if (filteredSymbols.Count == 0)
                {
                    _state.AppendLog("warning", "discover.empty", $"No configured dataset symbols discovered for {exchange}", new Dictionary<string, object?>
                    {
                        ["exchange"] = exchange,
                        ["discoveredSymbols"] = discoveredSymbols.Count,
                        ["configuredSymbols"] = configuredSymbols.Count,
                    });
                    continue;
                }

                result[exchange] = filteredSymbols;
                _state.AppendLog("info", "discover.exchange", $"Discovered {filteredSymbols.Count} configured symbols for {exchange}", new Dictionary<string, object?>
                {
                    ["exchange"] = exchange,
                    ["discoveredSymbols"] = discoveredSymbols.Count,
                    ["symbols"] = filteredSymbols.Count,
                });
            }
            catch (Exception ex)
            {
                var fallback = await TryLoadFallbackSymbolsAsync(exchange, ct);
                var filteredFallback = FilterConfiguredSymbols(fallback, configuredSymbols);
                if (filteredFallback.Count > 0)
                {
                    result[exchange] = filteredFallback;
                    _state.MarkDegraded($"{exchange} discovery failed; using persisted watcher state", ex.Message);
                    _state.AppendLog("warning", "discover.fallback", $"Using persisted symbol list for {exchange}", new Dictionary<string, object?>
                    {
                        ["exchange"] = exchange,
                        ["persistedSymbols"] = fallback.Count,
                        ["symbols"] = filteredFallback.Count,
                        ["error"] = ex.Message,
                    });
                    _log.LogWarning(ex, "Market watcher discovery failed for {Exchange}; using {Count} configured persisted symbols", exchange, filteredFallback.Count);
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

    private static async Task<IReadOnlyList<MarketWatchSymbol>> FetchMarketWatchSymbolsAsync(
        IMarketDataClient client,
        string exchange,
        HashSet<string> configuredSymbols,
        CancellationToken ct)
    {
        _ = configuredSymbols;
        using var discoveryCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        discoveryCts.CancelAfter(DiscoveryDeadline);

        try
        {
            return await client.FetchMarketWatchSymbolsAsync(discoveryCts.Token);
        }
        catch (OperationCanceledException ex) when (!ct.IsCancellationRequested && discoveryCts.IsCancellationRequested)
        {
            throw new TimeoutException(
                $"Market watcher discovery for {exchange} exceeded {DiscoveryDeadline.TotalSeconds:0}s",
                ex);
        }
    }

    private async Task<IReadOnlyList<MarketWatchSymbol>> TryLoadFallbackSymbolsAsync(string exchange, CancellationToken ct)
    {
        if (string.Equals(exchange, BinanceApiClient.ExchangeName, StringComparison.OrdinalIgnoreCase)
            || string.Equals(exchange, "bybit", StringComparison.OrdinalIgnoreCase))
        {
            return await _repo.ListKnownSymbolsAsync(exchange, ct);
        }

        return Array.Empty<MarketWatchSymbol>();
    }

    private async Task<SubscriptionStartResult> StartSubscriptionsAsync(
        IReadOnlyDictionary<string, IReadOnlyList<MarketWatchSymbol>> universe,
        MarketWatchSettings settings,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var handles = new List<IAsyncDisposable>();
        var activeExchanges = new List<string>();
        var failures = new List<string>();
        var warnings = new List<string>();

        async Task TryStartAsync(
            string exchange,
            Func<Task<IAsyncDisposable>> factory)
        {
            Exception? lastError = null;
            for (var attempt = 1; attempt <= SubscriptionStartAttempts; attempt++)
            {
                try
                {
                    handles.Add(await factory());
                    activeExchanges.Add(exchange);
                    _state.AppendLog("success", "subscribe.exchange", $"Subscriptions active for {exchange}", new Dictionary<string, object?>
                    {
                        ["exchange"] = exchange,
                        ["attempt"] = attempt,
                    });
                    return;
                }
                catch (Exception ex) when (!ct.IsCancellationRequested)
                {
                    lastError = ex;

                    if (attempt < SubscriptionStartAttempts)
                    {
                        _state.AppendLog("warning", "subscribe.retry", $"Retrying subscription for {exchange}", new Dictionary<string, object?>
                        {
                            ["exchange"] = exchange,
                            ["attempt"] = attempt,
                            ["maxAttempts"] = SubscriptionStartAttempts,
                            ["error"] = ex.Message,
                        });
                        _log.LogWarning(ex,
                            "Market watcher subscription start failed for {Exchange} on attempt {Attempt}/{MaxAttempts}; retrying",
                            exchange, attempt, SubscriptionStartAttempts);
                        await Task.Delay(SubscriptionRetryDelay, ct);
                        continue;
                    }
                }
            }

            var message = lastError?.Message ?? "unknown subscription start failure";
            var failure = $"{exchange}: {message}";

            if (handles.Count > 0 && IsNonFatalStartupFailure(exchange, lastError))
            {
                warnings.Add(failure);
                _state.AppendLog("warning", "subscribe.degraded", $"Continuing market watcher without {exchange}", new Dictionary<string, object?>
                {
                    ["exchange"] = exchange,
                    ["attempts"] = SubscriptionStartAttempts,
                    ["error"] = message,
                });
                _log.LogWarning(lastError, "Market watcher continuing without {Exchange} after non-fatal startup failure", exchange);
                return;
            }

            failures.Add(failure);
            _state.AppendLog("error", "subscribe.failed", $"Subscription failed for {exchange}", new Dictionary<string, object?>
            {
                ["exchange"] = exchange,
                ["attempts"] = SubscriptionStartAttempts,
                ["error"] = message,
            });
            _log.LogError(lastError, "Market watcher subscription failed for {Exchange} after {Attempts} attempts", exchange, SubscriptionStartAttempts);
        }

        if (universe.TryGetValue("binance", out var binanceSymbols) && binanceSymbols.Count > 0)
        {
            await TryStartAsync("binance", () => StartBinanceAsync(binanceSymbols, onPrice, ct));
        }

        if (universe.TryGetValue("bybit", out var bybitSymbols) && bybitSymbols.Count > 0)
        {
            await TryStartAsync("bybit", () => StartBybitAsync(bybitSymbols, settings, onPrice, ct));
        }

        if (handles.Count == 0)
        {
            throw new InvalidOperationException($"No market watcher subscriptions started: {string.Join("; ", failures)}");
        }

        if (failures.Count > 0)
        {
            foreach (var handle in handles)
            {
                await handle.DisposeAsync();
            }

            throw new InvalidOperationException($"Some subscriptions failed: {string.Join("; ", failures)}");
        }

        return new SubscriptionStartResult(
            new CompositeAsyncDisposable(handles),
            activeExchanges.ToArray(),
            warnings.ToArray());
    }

    private static bool IsNonFatalStartupFailure(string exchange, Exception? error)
    {
        // After Kraken was removed, no remaining exchange has a startup-time
        // failure mode we want to demote to a warning. Both Bybit and Binance
        // expose stable enough WS endpoints that a failed subscribe is fatal.
        _ = exchange;
        _ = error;
        return false;
    }

    private static async Task<IAsyncDisposable> StartBinanceAsync(
        IReadOnlyList<MarketWatchSymbol> symbols,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var socketClient = new BinanceSocketClient();
        var allowedSymbols = symbols
            .Select(item => item.RealtimeSymbol ?? item.Symbol)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        // Fixed-cadence mark-price stream: a single subscription delivers every
        // USDⓈ-M perpetual's mark price on a regular 1s interval regardless of
        // book activity, so idle symbols no longer accrue lag. Replaces the
        // event-driven bookTicker stream (which only pushed on best bid/ask
        // changes, leaving thin symbols stale for seconds).
        var subscription = await socketClient.UsdFuturesApi.ExchangeData.SubscribeToAllMarkPriceUpdatesAsync(
            1000,
            update =>
            {
                foreach (var item in update.Data)
                {
                    if (string.IsNullOrWhiteSpace(item.Symbol)
                        || !allowedSymbols.Contains(item.Symbol)
                        || item.MarkPrice <= 0)
                    {
                        continue;
                    }

                    onPrice("binance", item.Symbol, item.MarkPrice, DateTimeOffset.UtcNow);
                }
            },
            ct);

        if (!subscription.Success)
        {
            socketClient.Dispose();
            throw new InvalidOperationException($"Binance mark price subscription failed: {subscription.Error}");
        }

        return new CompositeAsyncDisposable(new IAsyncDisposable[]
        {
            new AsyncDisposeAction(() => new ValueTask(socketClient.UnsubscribeAsync(subscription.Data))),
            new AsyncDisposeAction(() =>
            {
                socketClient.Dispose();
                return ValueTask.CompletedTask;
            }),
        });
    }

    private static async Task<IAsyncDisposable> StartBybitAsync(
        IReadOnlyList<MarketWatchSymbol> symbols,
        MarketWatchSettings settings,
        Action<string, string, decimal, DateTimeOffset> onPrice,
        CancellationToken ct)
    {
        var socketClient = new BybitSocketClient();
        var disposers = new List<Func<ValueTask>>();

        // Ticker stream (≈100ms cadence) instead of the event-driven depth-1
        // orderbook: it carries best bid/ask (so the price stays a mid) plus
        // last/mark as fallbacks, and pushes far more regularly for thin books.
        foreach (var chunk in symbols
            .Select(item => item.RealtimeSymbol ?? item.Symbol)
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .Chunk(Math.Max(1, settings.BybitSymbolsPerSubscription)))
        {
            var subscription = await socketClient.V5LinearApi.SubscribeToTickerUpdatesAsync(chunk, update =>
            {
                if (string.IsNullOrWhiteSpace(update.Data.Symbol))
                {
                    return;
                }

                // Ticker deltas carry only changed fields, so prefer a bid/ask
                // mid, then last price, then mark price.
                var price = SelectMidPrice(update.Data.BestBidPrice, update.Data.BestAskPrice)
                    ?? update.Data.LastPrice
                    ?? update.Data.MarkPrice;
                if (!price.HasValue || price.Value <= 0)
                {
                    return;
                }

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

    private static decimal? SelectMidPrice(decimal? bestBidPrice, decimal? bestAskPrice)
    {
        var bid = bestBidPrice.GetValueOrDefault();
        var ask = bestAskPrice.GetValueOrDefault();
        var hasBid = bid > 0;
        var hasAsk = ask > 0;

        if (hasBid && hasAsk)
        {
            return (bid + ask) / 2m;
        }

        if (hasBid)
        {
            return bid;
        }

        if (hasAsk)
        {
            return ask;
        }

        return null;
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

    private async Task PersistClosedCandlesAsync(
        IReadOnlyList<PendingSnapshot> pending,
        CancellationToken ct)
    {
        var closedCandles = pending
            .SelectMany(item => item.ClosedCandles)
            .ToArray();

        if (closedCandles.Length == 0)
        {
            return;
        }

        foreach (var group in closedCandles.GroupBy(
            item => DatasetCore.MakeTableName(item.Symbol, item.Timeframe, item.Exchange),
            StringComparer.OrdinalIgnoreCase))
        {
            await PersistClosedCandleGroupAsync(
                group.Key,
                group.OrderBy(item => item.Candle.BucketStartMs).ToArray(),
                ct);
        }
    }

    private async Task PersistClosedCandleGroupAsync(
        string tableName,
        IReadOnlyList<ClosedDatasetCandle> closedCandles,
        CancellationToken ct)
    {
        if (closedCandles.Count == 0)
        {
            return;
        }

        const int rsiPeriod = 14;
        const long fundingIntervalMs = 28_800_000L;

        var first = closedCandles[0];
        var (timeframeKey, interval, stepMs) = DatasetCore.NormalizeTimeframe(first.Timeframe);
        var targetTimestamps = closedCandles
            .Select(item => item.Candle.BucketStartMs)
            .Distinct()
            .OrderBy(item => item)
            .ToArray();

        if (targetTimestamps.Length == 0)
        {
            return;
        }

        var warmupCandles = Math.Max(DatasetConstants.DefaultWarmupCandles, rsiPeriod * 2);
        var fetchStart = Math.Max(0L, targetTimestamps[0] - warmupCandles * stepMs);
        var fetchEnd = targetTimestamps[^1];
        var symbol = first.Symbol.ToUpperInvariant();
        var exchange = first.Exchange;
        var market = _marketDataClientFactory.GetRequiredClient(exchange);
        var (oiLabel, oiIntervalMs) = DatasetCore.ChooseOpenInterestInterval(stepMs);

        var klineTask = market.FetchKlinesAsync(symbol, interval, fetchStart, fetchEnd, stepMs, 1, ct);
        var fundingTask = market.FetchFundingRatesAsync(symbol, Math.Max(0L, targetTimestamps[0] - fundingIntervalMs), fetchEnd, fundingIntervalMs, ct);
        var oiTask = market.FetchOpenInterestAsync(symbol, oiLabel, Math.Max(0L, targetTimestamps[0] - oiIntervalMs), fetchEnd, oiIntervalMs, ct);

        var klines = await klineTask;
        var funding = await fundingTask;
        var openInterest = await oiTask;

        var klinesByTs = klines
            .GroupBy(item => item.TimestampMs)
            .ToDictionary(group => group.Key, group => group.Last());
        var rsiByTs = ComputeWilderRsi(
            klines
                .OrderBy(item => item.TimestampMs)
                .Select(item => (item.TimestampMs, item.Close))
                .ToList(),
            rsiPeriod);
        var fundingFf = BuildForwardFill(funding.Select(item => (item.TimestampMs, item.Rate)).ToArray());
        var oiFf = BuildForwardFill(openInterest.Select(item => (item.TimestampMs, item.Oi)).ToArray());

        var rows = new List<DatasetRepository.MarketRow>(targetTimestamps.Length);
        foreach (var timestampMs in targetTimestamps)
        {
            if (!klinesByTs.TryGetValue(timestampMs, out var kline))
            {
                continue;
            }

            rows.Add(new DatasetRepository.MarketRow(
                TimestampMs: timestampMs,
                Symbol: symbol,
                Exchange: exchange,
                Timeframe: timeframeKey,
                OpenPrice: kline.Open,
                HighPrice: kline.High,
                LowPrice: kline.Low,
                ClosePrice: kline.Close,
                Volume: kline.Volume,
                Turnover: kline.Turnover,
                FundingRate: LookupForwardFill(fundingFf, timestampMs),
                OpenInterest: LookupForwardFill(oiFf, timestampMs),
                Rsi: rsiByTs.TryGetValue(timestampMs, out var rsi) ? rsi : null));
        }

        if (rows.Count != targetTimestamps.Length)
        {
            var missing = targetTimestamps.Length - rows.Count;
            // Recoverable: the missing candles will be re-attempted on the next
            // watcher tick (the live state still holds the open bucket, and the
            // exchange will fill in the trailing base candle that was withheld
            // as still-forming on this round). Throwing here used to crash the
            // entire watcher loop for *every* exchange — a single per-exchange
            // miss would lose ticks across the board. Demote to a warning +
            // skip this table; partial rows are not persisted to avoid feature
            // gaps.
            _log.LogWarning(
                "Market watcher could not hydrate {Missing}/{Total} closed candles for {Table}; skipping this flush",
                missing, targetTimestamps.Length, tableName);
            _state.AppendLog(
                "warning",
                "persist.hydrate_miss",
                $"Skipped {missing}/{targetTimestamps.Length} closed candles for {tableName} (will retry next tick)",
                new Dictionary<string, object?>
                {
                    ["table"] = tableName,
                    ["missing"] = missing,
                    ["target"] = targetTimestamps.Length,
                });
            if (rows.Count == 0)
            {
                return;
            }
            // Persist only the rows we successfully hydrated, recomputing the
            // target list so feature back-fill stops at the last good row.
            targetTimestamps = rows.Select(r => r.TimestampMs).OrderBy(t => t).ToArray();
        }

        await _datasetRepo.CreateTableIfNotExistsAsync(tableName, ct);
        await _datasetRepo.BulkUpsertAsync(tableName, rows, ct);
        await _datasetRepo.ComputeAndUpdateFeaturesSinceAsync(tableName, targetTimestamps[0], ct);
    }

    private static Dictionary<long, decimal> ComputeWilderRsi(IList<(long Ts, decimal Close)> closes, int period)
    {
        var result = new Dictionary<long, decimal>();
        if (closes.Count < period + 1) return result;

        decimal gainSum = 0;
        decimal lossSum = 0;
        for (int i = 1; i <= period; i++)
        {
            var diff = closes[i].Close - closes[i - 1].Close;
            if (diff > 0)
            {
                gainSum += diff;
            }
            else
            {
                lossSum -= diff;
            }
        }

        var avgGain = gainSum / period;
        var avgLoss = lossSum / period;
        result[closes[period].Ts] = avgLoss == 0 ? 100m : 100m - 100m / (1m + avgGain / avgLoss);
        for (int i = period + 1; i < closes.Count; i++)
        {
            var diff = closes[i].Close - closes[i - 1].Close;
            var gain = diff > 0 ? diff : 0m;
            var loss = diff < 0 ? -diff : 0m;
            avgGain = (avgGain * (period - 1) + gain) / period;
            avgLoss = (avgLoss * (period - 1) + loss) / period;
            result[closes[i].Ts] = avgLoss == 0 ? 100m : 100m - 100m / (1m + avgGain / avgLoss);
        }

        return result;
    }

    private static List<(long Ts, decimal? Value)> BuildForwardFill(IReadOnlyList<(long Ts, decimal Value)> src)
    {
        return src
            .OrderBy(item => item.Ts)
            .Select(item => (item.Ts, (decimal?)item.Value))
            .ToList();
    }

    private static decimal? LookupForwardFill(List<(long Ts, decimal? Value)> src, long ts)
    {
        if (src.Count == 0) return null;

        int lo = 0;
        int hi = src.Count - 1;
        int best = -1;
        while (lo <= hi)
        {
            int mid = (lo + hi) >> 1;
            if (src[mid].Ts <= ts)
            {
                best = mid;
                lo = mid + 1;
            }
            else
            {
                hi = mid - 1;
            }
        }

        return best >= 0 ? src[best].Value : null;
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

    private async Task<IReadOnlyList<string>> LoadCenterSymbolsAsync(CancellationToken ct)
    {
        try
        {
            return await _pairsRepo.GetActiveSymbolsAsync(ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to load symbols from currency-pairs center; falling back to settings whitelist");
            return Array.Empty<string>();
        }
    }

    private static HashSet<string> NormalizeSymbols(IEnumerable<string>? configured)
    {
        var requested = configured?
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Select(item => item.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var effective = requested is { Length: > 0 }
            ? requested
            : DatasetConstants.SupportedSymbols;

        return new HashSet<string>(effective, StringComparer.OrdinalIgnoreCase);
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
            : new HashSet<string>(MarketWatchSettings.DefaultLiveExchanges, StringComparer.OrdinalIgnoreCase);
    }

    private static IReadOnlyList<MarketWatchSymbol> FilterConfiguredSymbols(
        IReadOnlyList<MarketWatchSymbol> discovered,
        HashSet<string> configuredSymbols)
    {
        if (discovered.Count == 0 || configuredSymbols.Count == 0)
        {
            return Array.Empty<MarketWatchSymbol>();
        }

        return discovered
            .Where(item => !string.IsNullOrWhiteSpace(item.Symbol)
                && configuredSymbols.Contains(item.Symbol))
            .GroupBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First())
            .OrderBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private sealed record PendingSnapshot(
        MarketWatchSymbolSnapshot Snapshot,
        SymbolLiveState State,
        long Version,
        IReadOnlyList<ClosedDatasetCandle> ClosedCandles);

    private sealed record ClosedDatasetCandle(
        string Exchange,
        string Symbol,
        string Timeframe,
        MarketWatchCandleSnapshot Candle);

    private sealed record SubscriptionStartResult(
        IAsyncDisposable Handle,
        IReadOnlyList<string> ActiveExchanges,
        IReadOnlyList<string> Warnings);

    private sealed class SymbolLiveState
    {
        private readonly object _gate = new();
        private readonly Dictionary<string, MarketWatchCandleSnapshot> _activeCandles = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, MarketWatchCandleSnapshot> _closedCandles = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, MarketWatchCandleSnapshot> _pendingClosedCandles = new(StringComparer.OrdinalIgnoreCase);
        private readonly string _exchange;
        private readonly string _symbol;
        private readonly string? _realtimeSymbol;
        private long _persistVersion;
        private bool _dirty;
        private decimal _lastPrice;
        private DateTimeOffset _lastPriceTimestampUtc;

        public SymbolLiveState(string exchange, string symbol, string? realtimeSymbol)
        {
            _exchange = exchange;
            _symbol = symbol;
            _realtimeSymbol = realtimeSymbol;
        }

        public MarketWatcherLiveRowSnapshot Apply(decimal price, DateTimeOffset timestampUtc, IReadOnlyDictionary<string, long> timeframes)
        {
            lock (_gate)
            {
                var isFirstTick = _lastPriceTimestampUtc == default;
                var closedCandlesUpdated = false;
                _lastPrice = price;
                _lastPriceTimestampUtc = timestampUtc;
                var timestampMs = timestampUtc.ToUnixTimeMilliseconds();

                foreach (var timeframe in timeframes)
                {
                    var bucketStartMs = timestampMs - timestampMs % timeframe.Value;
                    if (!_activeCandles.TryGetValue(timeframe.Key, out var candle))
                    {
                        _activeCandles[timeframe.Key] = new MarketWatchCandleSnapshot(
                            BucketStartMs: bucketStartMs,
                            Open: price,
                            High: price,
                            Low: price,
                            Close: price,
                            LastUpdateMs: timestampMs);
                        continue;
                    }

                    if (candle.BucketStartMs != bucketStartMs)
                    {
                        _closedCandles[timeframe.Key] = candle;
                        _pendingClosedCandles[timeframe.Key] = candle;
                        _activeCandles[timeframe.Key] = new MarketWatchCandleSnapshot(
                            BucketStartMs: bucketStartMs,
                            Open: price,
                            High: price,
                            Low: price,
                            Close: price,
                            LastUpdateMs: timestampMs);
                        closedCandlesUpdated = true;
                        continue;
                    }

                    _activeCandles[timeframe.Key] = candle with
                    {
                        High = Math.Max(candle.High, price),
                        Low = Math.Min(candle.Low, price),
                        Close = price,
                        LastUpdateMs = timestampMs,
                    };
                }

                if (isFirstTick || closedCandlesUpdated)
                {
                    _dirty = true;
                    _persistVersion++;
                }

                return new MarketWatcherLiveRowSnapshot(
                    Exchange: _exchange,
                    Symbol: _symbol,
                    RealtimeSymbol: _realtimeSymbol,
                    LastPrice: _lastPrice,
                    LastPriceTimestampMs: timestampMs,
                    UpdatedAtMs: timestampMs,
                    Frames: _activeCandles.Keys.OrderBy(item => item, StringComparer.OrdinalIgnoreCase).ToArray());
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

                var candlesCopy = new Dictionary<string, MarketWatchCandleSnapshot>(_closedCandles, StringComparer.OrdinalIgnoreCase);
                var pendingClosedCopy = _pendingClosedCandles
                    .Select(item => new ClosedDatasetCandle(
                        _exchange,
                        _symbol,
                        item.Key,
                        item.Value))
                    .ToArray();
                return new PendingSnapshot(
                    new MarketWatchSymbolSnapshot(
                        Exchange: _exchange,
                        Symbol: _symbol,
                        RealtimeSymbol: _realtimeSymbol,
                        LastPrice: _lastPrice,
                        LastPriceTimestampUtc: _lastPriceTimestampUtc,
                        CandlesJson: JsonSerializer.Serialize(candlesCopy, JsonOptions)),
                    this,
                    _persistVersion,
                    pendingClosedCopy);
            }
        }

        public void MarkPersisted(long persistedVersion)
        {
            lock (_gate)
            {
                if (_persistVersion == persistedVersion)
                {
                    _dirty = false;
                    _pendingClosedCandles.Clear();
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