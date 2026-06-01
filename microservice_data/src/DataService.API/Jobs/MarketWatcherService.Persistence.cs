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
}
