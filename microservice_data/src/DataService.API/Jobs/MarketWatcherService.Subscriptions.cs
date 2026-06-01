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

public sealed partial class MarketWatcherService
{
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

    private sealed record SubscriptionStartResult(
        IAsyncDisposable Handle,
        IReadOnlyList<string> ActiveExchanges,
        IReadOnlyList<string> Warnings);

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
