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
}
