using ccxt;
using DataService.API.Dataset;

namespace DataService.API.Markets;

/// <summary>
/// <see cref="IMarketDataClient"/> backed by the unified <c>ccxt</c> library
/// (official NuGet package <c>ccxt</c>). One instance wraps one ccxt
/// <see cref="ccxt.Exchange"/> (e.g. <c>"bybit"</c>, <c>"binance"</c>),
/// configured for USDT-margined linear perpetuals (<c>options.defaultType =
/// "swap"</c>). This lets the dataset pipeline ingest OHLCV / funding / open
/// interest from any ccxt-supported exchange without a hand-written REST
/// adapter — adding an exchange becomes a config entry rather than new code.
///
/// Implementation notes / trade-offs vs the hand-written
/// <see cref="DataService.API.Bybit.BybitApiClient"/> /
/// <see cref="BinanceApiClient"/> REST adapters:
/// <list type="bullet">
///   <item>ccxt's unified OHLCV is <c>[ts, open, high, low, close, volume]</c>
///   with NO turnover / quote-volume column, so <c>turnover</c> is
///   APPROXIMATED as <c>close * volume</c>.</item>
///   <item>ccxt returns <see cref="double"/> values, so prices/volumes carry
///   float precision rather than the exact decimal string the raw REST API
///   returns. For ML datasets this is acceptable; flip
///   <c>DataService:Dataset:OhlcvProvider=native</c> to fall back to the exact
///   adapters.</item>
///   <item>Symbol format is mapped <c>BTCUSDT ⇄ BTC/USDT:USDT</c> at the
///   boundary; internal storage/table naming stays <c>BTCUSDT</c>.</item>
///   <item>Per-page fan-out is preserved, but ccxt's own rate limiter
///   (<c>enableRateLimit</c>) serializes the underlying HTTP, so concurrency
///   mostly bounds queue depth rather than true parallelism.</item>
/// </list>
/// </summary>
public sealed class CcxtMarketDataClient : IMarketDataClient
{
    private readonly ccxt.Exchange _ex;
    private readonly ILogger _log;

    /// <summary>step_ms → ccxt unified timeframe string.</summary>
    private static readonly IReadOnlyDictionary<long, string> StepToTimeframe =
        new Dictionary<long, string>
        {
            [60_000]     = "1m",
            [180_000]    = "3m",
            [300_000]    = "5m",
            [900_000]    = "15m",
            [1_800_000]  = "30m",
            [3_600_000]  = "1h",
            [7_200_000]  = "2h",
            [14_400_000] = "4h",
            [21_600_000] = "6h",
            [43_200_000] = "12h",
            [86_400_000] = "1d",
        };

    /// <summary>open-interest interval_ms → ccxt unified timeframe string.</summary>
    private static readonly IReadOnlyDictionary<long, string> OiIntervalToTimeframe =
        new Dictionary<long, string>
        {
            [300_000]    = "5m",
            [900_000]    = "15m",
            [1_800_000]  = "30m",
            [3_600_000]  = "1h",
            [14_400_000] = "4h",
            [86_400_000] = "1d",
        };

    /// <summary>Quote/settle currencies we know how to split a concatenated
    /// symbol on (longest-match first). The dataset universe is *USDT.</summary>
    private static readonly string[] KnownQuotes = { "USDT", "USDC", "USD" };

    public CcxtMarketDataClient(string exchangeId, ILogger log)
    {
        Exchange = (exchangeId ?? throw new ArgumentNullException(nameof(exchangeId)))
            .Trim().ToLowerInvariant();
        if (Exchange.Length == 0) throw new ArgumentException("exchangeId is empty", nameof(exchangeId));
        _log = log;

        // ccxt instance config: linear perps, client-side rate limiting, and the
        // same per-request timeout the native adapters use.
        var config = new Dictionary<string, object>
        {
            ["enableRateLimit"] = true,
            ["timeout"]         = DatasetConstants.RequestTimeoutSeconds * 1000,
            ["options"]         = new Dictionary<string, object> { ["defaultType"] = "swap" },
        };
        _ex = ccxt.Exchange.DynamicallyCreateInstance(Exchange, config);
    }

    public string Exchange { get; }

    // ── Symbol / timeframe mapping ──────────────────────────────────────────

    /// <summary>Map a concatenated exchange symbol (<c>BTCUSDT</c>) to a ccxt
    /// unified linear-perpetual symbol (<c>BTC/USDT:USDT</c>). Already-unified
    /// inputs are passed through.</summary>
    internal static string ToUnifiedSymbol(string raw)
    {
        var s = (raw ?? string.Empty).Trim().ToUpperInvariant();
        if (s.Length == 0) return s;
        if (s.Contains('/')) return s; // already unified
        foreach (var q in KnownQuotes)
        {
            if (s.Length > q.Length && s.EndsWith(q, StringComparison.Ordinal))
            {
                var baseCcy = s[..^q.Length];
                return $"{baseCcy}/{q}:{q}"; // linear perpetual, settle == quote
            }
        }
        return $"{s}/USDT:USDT"; // sensible default for the *USDT universe
    }

    private static string MapTimeframe(long stepMs) =>
        StepToTimeframe.TryGetValue(stepMs, out var tf)
            ? tf
            : throw new ArgumentException($"unsupported step_ms for ccxt: {stepMs}", nameof(stepMs));

    private static decimal? ToDec(double? d)
    {
        if (d is null || double.IsNaN(d.Value) || double.IsInfinity(d.Value)) return null;
        try { return (decimal)d.Value; }
        catch (OverflowException) { return null; }
    }

    // ── Market watch symbols ────────────────────────────────────────────────

    /// <summary>List active USDT-margined linear-swap symbols (in <c>BTCUSDT</c>
    /// form) discovered via <c>ccxt.fetchMarkets</c>.</summary>
    public async Task<IReadOnlyList<MarketWatchSymbol>> FetchMarketWatchSymbolsAsync(
        CancellationToken ct = default)
    {
        var markets = await _ex.FetchMarkets();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var result = new List<MarketWatchSymbol>(markets?.Count ?? 0);
        if (markets is null) return result;

        foreach (var m in markets)
        {
            if (m.swap != true || m.linear != true) continue;
            if (m.active == false) continue;
            var quote = m.quote ?? string.Empty;
            var settle = m.settle ?? string.Empty;
            if (!quote.Equals("USDT", StringComparison.OrdinalIgnoreCase)
                && !settle.Equals("USDT", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            // Exchange-native id (e.g. "BTCUSDT"); fall back to base+quote.
            var id = m.uppercaseId;
            if (string.IsNullOrWhiteSpace(id))
            {
                id = $"{m.baseCurrency}{m.quote}".ToUpperInvariant();
            }
            if (string.IsNullOrWhiteSpace(id)) continue;
            if (seen.Add(id)) result.Add(new MarketWatchSymbol(id));
        }
        return result;
    }

    /// <summary>ccxt does not expose a uniform launch-time / funding-interval
    /// lookup, so we return neutral defaults: launch 0 (no window clamp) and an
    /// 8h funding interval. The ingest window is still bounded by the requested
    /// range, so a 0 launch only means a few empty leading pages at most.</summary>
    public Task<(long LaunchMs, long FundingMs)> FetchInstrumentDetailsAsync(
        string category, string symbol, CancellationToken ct = default)
        => Task.FromResult((0L, 28_800_000L));

    // ── OHLCV klines ────────────────────────────────────────────────────────

    public async Task<IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>>
        FetchKlinesAsync(
            string symbol, string interval, long startMs, long endMs, long stepMs,
            int maxParallel = 0, CancellationToken ct = default,
            Action<int, int>? onPageDone = null)
    {
        if (stepMs <= 0) throw new ArgumentException("stepMs must be positive", nameof(stepMs));
        if (endMs < startMs) return Array.Empty<(long, decimal, decimal, decimal, decimal, decimal, decimal)>();

        var tf = MapTimeframe(stepMs);            // interval arg is exchange-specific; ccxt uses tf derived from stepMs
        var unified = ToUnifiedSymbol(symbol);

        // Tile the range into windows of PageLimitKline candles, same as the
        // native adapters, so progress reporting + concurrency stay identical.
        var pageSpan = DatasetConstants.PageLimitKline * stepMs;
        var windows = new List<(long Start, long End)>();
        for (long s = startMs; s <= endMs; s += pageSpan)
        {
            var e = Math.Min(endMs, s + pageSpan - stepMs);
            windows.Add((s, e));
        }

        var totalPages = windows.Count;
        var completedPages = 0;
        var degree = maxParallel > 0 ? maxParallel : DatasetConstants.MaxParallelApiWorkers;
        using var gate = new SemaphoreSlim(degree, degree);

        var tasks = windows.Select(async w =>
        {
            await gate.WaitAsync(ct);
            try
            {
                var page = new List<(long, decimal, decimal, decimal, decimal, decimal, decimal)>(
                    DatasetConstants.PageLimitKline);
                long cursor = w.Start;
                // Inner continuation loop makes us correct for exchanges whose
                // per-call limit is below PageLimitKline; bybit/binance fill a
                // window in a single call. Guard caps pathological loops.
                for (int guard = 0; cursor <= w.End && guard < 10_000; guard++)
                {
                    ct.ThrowIfCancellationRequested();
                    var candles = await _ex.FetchOHLCV(unified, tf, cursor, DatasetConstants.PageLimitKline);
                    if (candles is null || candles.Count == 0) break;

                    long maxTs = cursor;
                    foreach (var c in candles)
                    {
                        if (c.timestamp is null) continue;
                        var ts = c.timestamp.Value;
                        if (ts > maxTs) maxTs = ts;
                        if (ts < w.Start || ts > w.End) continue;

                        var o  = ToDec(c.open);
                        var h  = ToDec(c.high);
                        var l  = ToDec(c.low);
                        var cl = ToDec(c.close);
                        var v  = ToDec(c.volume);
                        if (o is null || h is null || l is null || cl is null || v is null) continue;

                        // No quote-volume in unified OHLCV → approximate turnover.
                        var turnover = cl.Value * v.Value;
                        page.Add((ts, o.Value, h.Value, l.Value, cl.Value, v.Value, turnover));
                    }

                    if (maxTs <= cursor) break;     // no forward progress → stop
                    cursor = maxTs + stepMs;
                }
                return page;
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                _log.LogWarning(ex,
                    "ccxt FetchOHLCV failed on {Exchange} {Symbol} {Tf} window {Start}-{End}",
                    Exchange, unified, tf, w.Start, w.End);
                return new List<(long, decimal, decimal, decimal, decimal, decimal, decimal)>();
            }
            finally
            {
                gate.Release();
                if (onPageDone is not null)
                {
                    var done = Interlocked.Increment(ref completedPages);
                    try { onPageDone(done, totalPages); } catch { /* progress must not break ingest */ }
                }
            }
        });

        var pages = await Task.WhenAll(tasks);
        var merged = new Dictionary<long, (decimal, decimal, decimal, decimal, decimal, decimal)>(
            pages.Sum(p => p.Count));
        foreach (var p in pages)
            foreach (var (t, o, h, l, c, v, tv) in p) merged[t] = (o, h, l, c, v, tv);

        return merged
            .Where(kv => kv.Key >= startMs && kv.Key <= endMs)
            .OrderBy(kv => kv.Key)
            .Select(kv => (kv.Key, kv.Value.Item1, kv.Value.Item2, kv.Value.Item3,
                           kv.Value.Item4, kv.Value.Item5, kv.Value.Item6))
            .ToList();
    }

    // ── Funding rate history ────────────────────────────────────────────────

    public async Task<IReadOnlyList<(long TimestampMs, decimal Rate)>>
        FetchFundingRatesAsync(
            string symbol, long startMs, long endMs,
            long fundingIntervalMs = 28_800_000L,
            CancellationToken ct = default)
    {
        if (endMs < startMs) return Array.Empty<(long, decimal)>();
        var unified = ToUnifiedSymbol(symbol);

        var merged = new Dictionary<long, decimal>();
        long cursor = startMs;
        for (int guard = 0; cursor <= endMs && guard < 10_000; guard++)
        {
            ct.ThrowIfCancellationRequested();
            List<FundingRateHistory> list;
            try
            {
                list = await _ex.FetchFundingRateHistory(unified, cursor, DatasetConstants.PageLimitFunding);
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "ccxt FetchFundingRateHistory failed on {Exchange} {Symbol} from {Cursor}",
                    Exchange, unified, cursor);
                break;
            }
            if (list is null || list.Count == 0) break;

            long maxTs = cursor;
            foreach (var f in list)
            {
                if (f.timestamp is null) continue;
                var ts = f.timestamp.Value;
                if (ts > maxTs) maxTs = ts;
                if (ts < startMs || ts > endMs) continue;
                var rate = ToDec(f.fundingRate);
                if (rate is null) continue;
                merged[ts] = rate.Value;
            }

            if (maxTs <= cursor) break;
            cursor = maxTs + 1;
        }

        return merged.Where(kv => kv.Key >= startMs && kv.Key <= endMs)
                     .OrderBy(kv => kv.Key)
                     .Select(kv => (kv.Key, kv.Value))
                     .ToList();
    }

    // ── Open interest history ───────────────────────────────────────────────

    public async Task<IReadOnlyList<(long TimestampMs, decimal Oi)>>
        FetchOpenInterestAsync(
            string symbol, string intervalLabel, long startMs, long endMs,
            long intervalMs,
            CancellationToken ct = default,
            Action<int, int>? onPageDone = null)
    {
        if (endMs < startMs) return Array.Empty<(long, decimal)>();
        if (intervalMs <= 0) throw new ArgumentException("intervalMs must be positive", nameof(intervalMs));
        var unified = ToUnifiedSymbol(symbol);
        var oiTf = OiIntervalToTimeframe.TryGetValue(intervalMs, out var tf) ? tf : "1h";

        var merged = new Dictionary<long, decimal>();
        long cursor = startMs;
        int reported = 0;
        for (int guard = 0; cursor <= endMs && guard < 10_000; guard++)
        {
            ct.ThrowIfCancellationRequested();
            List<OpenInterest> list;
            try
            {
                list = await _ex.FetchOpenInterestHistory(unified, oiTf, cursor, DatasetConstants.PageLimitOpenInterest);
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "ccxt FetchOpenInterestHistory failed on {Exchange} {Symbol} {Tf} from {Cursor}",
                    Exchange, unified, oiTf, cursor);
                break;
            }
            if (list is null || list.Count == 0) break;

            long maxTs = cursor;
            foreach (var oi in list)
            {
                if (oi.timestamp is null) continue;
                var ts = oi.timestamp.Value;
                if (ts > maxTs) maxTs = ts;
                if (ts < startMs || ts > endMs) continue;
                // openInterestAmount = base-asset OI (matches the raw adapters'
                // openInterest); fall back to value if amount is absent.
                var amount = ToDec(oi.openInterestAmount) ?? ToDec(oi.openInterestValue);
                if (amount is null) continue;
                merged[ts] = amount.Value;
            }

            if (onPageDone is not null)
            {
                reported++;
                try { onPageDone(reported, reported); } catch { }
            }

            if (maxTs <= cursor) break;
            cursor = maxTs + intervalMs;
        }

        return merged.Where(kv => kv.Key >= startMs && kv.Key <= endMs)
                     .OrderBy(kv => kv.Key)
                     .Select(kv => (kv.Key, kv.Value))
                     .ToList();
    }
}
