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
}
