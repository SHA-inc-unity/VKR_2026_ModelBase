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

public sealed partial class MarketWatcherService : BackgroundService
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
            configuredSymbols.Count,
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
                    configuredSymbols.Count,
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
}
