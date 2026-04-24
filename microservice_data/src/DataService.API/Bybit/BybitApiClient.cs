using System.Collections.Concurrent;
using System.Text.Json;
using DataService.API.Dataset;

namespace DataService.API.Bybit;

/// <summary>
/// HTTP client for Bybit public REST API with retry and lock-free instrument cache.
/// Port of Python BybitApiClient.
/// </summary>
public sealed class BybitApiClient
{
    private readonly HttpClient _http;
    private readonly ILogger<BybitApiClient> _log;

    // key = "category:symbol", value = (launchTimeMs, fundingTimeMs)
    private readonly ConcurrentDictionary<string, (long LaunchMs, long FundingMs)>
        _instrumentCache = new();

    public BybitApiClient(HttpClient http, ILogger<BybitApiClient> log)
    {
        _http = http;
        _log  = log;
    }

    /// <summary>
    /// GET the given URL with retry, returning the parsed JSON document.
    /// Caller must dispose the returned JsonDocument.
    /// </summary>
    public async Task<JsonDocument> GetJsonAsync(string url, CancellationToken ct = default)
    {
        Exception? last = null;
        for (int attempt = 0; attempt < DatasetConstants.MaxRetries; attempt++)
        {
            HttpResponseMessage? response = null;
            try
            {
                response = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct);
                response.EnsureSuccessStatusCode();
                var doc = await JsonDocument.ParseAsync(
                    await response.Content.ReadAsStreamAsync(ct), cancellationToken: ct);
                return doc;
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                last = ex;
                _log.LogWarning(ex, "GET {Url} failed (attempt {A}/{Max})", url, attempt + 1, DatasetConstants.MaxRetries);
                await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt)), ct);
            }
            finally
            {
                response?.Dispose();
            }
        }
        throw new HttpRequestException($"GET {url} failed after {DatasetConstants.MaxRetries} attempts", last);
    }

    /// <summary>
    /// Fetch instrument launch time and funding interval for a symbol.
    /// Returns (launchTimeMs, fundingIntervalMs).
    /// Results are cached for the lifetime of this client.
    /// </summary>
    public async Task<(long LaunchMs, long FundingMs)> FetchInstrumentDetailsAsync(
        string category, string symbol, CancellationToken ct = default)
    {
        var cacheKey = $"{category}:{symbol}";
        if (_instrumentCache.TryGetValue(cacheKey, out var cached))
            return cached;

        var url = $"{DatasetConstants.BybitBaseUrl}/v5/market/instruments-info?category={category}&symbol={symbol}";
        using var doc = await GetJsonAsync(url, ct);
        var root = doc.RootElement;
        var item = root.GetProperty("result").GetProperty("list")[0];

        var launchMs  = item.TryGetProperty("launchTime",  out var lt) ? lt.GetInt64() : 0L;
        var fundingMs = item.TryGetProperty("fundingInterval", out var fi)
            ? fi.GetInt64() * 60_000L   // Bybit returns minutes
            : 480 * 60_000L;            // default 8 h

        var result = (launchMs, fundingMs);
        _instrumentCache.TryAdd(cacheKey, result);
        return result;
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    private static decimal? TryDec(in JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Number && e.TryGetDecimal(out var n)) return n;
        if (e.ValueKind == JsonValueKind.String && decimal.TryParse(
                e.GetString(),
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out var s)) return s;
        return null;
    }

    private static long? TryMs(in JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Number && e.TryGetInt64(out var n)) return n;
        if (e.ValueKind == JsonValueKind.String && long.TryParse(e.GetString(), out var s)) return s;
        return null;
    }

    // ── OHLCV klines ─────────────────────────────────────────────────────

    /// <summary>
    /// Fetch full OHLCV klines for <paramref name="symbol"/> from
    /// <c>/v5/market/kline</c> in range
    /// [<paramref name="startMs"/>, <paramref name="endMs"/>] with the given
    /// Bybit <paramref name="interval"/>. Pages are fetched in parallel by
    /// slicing the range into windows of <see cref="DatasetConstants.PageLimitKline"/>
    /// candles. Returns tuples sorted ascending.
    /// </summary>
    public async Task<IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>>
        FetchKlinesAsync(
            string symbol, string interval, long startMs, long endMs, long stepMs,
            int maxParallel = 0, CancellationToken ct = default,
            Action<int, int>? onPageDone = null)
    {
        if (stepMs <= 0) throw new ArgumentException("stepMs must be positive", nameof(stepMs));
        if (endMs < startMs) return Array.Empty<(long, decimal, decimal, decimal, decimal, decimal, decimal)>();

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
                var url = $"{DatasetConstants.BybitBaseUrl}/v5/market/kline"
                        + $"?category=linear&symbol={symbol}&interval={interval}"
                        + $"&start={w.Start}&end={w.End}&limit={DatasetConstants.PageLimitKline}";
                using var doc = await GetJsonAsync(url, ct);
                var list = doc.RootElement.GetProperty("result").GetProperty("list");
                var page = new List<(long, decimal, decimal, decimal, decimal, decimal, decimal)>(list.GetArrayLength());
                foreach (var item in list.EnumerateArray())
                {
                    // [startMs, open, high, low, close, volume, turnover]
                    if (item.ValueKind != JsonValueKind.Array || item.GetArrayLength() < 7) continue;
                    var ts       = TryMs(item[0]);
                    var open     = TryDec(item[1]);
                    var high     = TryDec(item[2]);
                    var low      = TryDec(item[3]);
                    var close    = TryDec(item[4]);
                    var volume   = TryDec(item[5]);
                    var turnover = TryDec(item[6]);
                    if (ts is null || open is null || high is null || low is null
                        || close is null || volume is null || turnover is null) continue;
                    page.Add((ts.Value, open.Value, high.Value, low.Value, close.Value, volume.Value, turnover.Value));
                }
                return page;
            }
            finally
            {
                gate.Release();
                if (onPageDone is not null)
                {
                    var done = Interlocked.Increment(ref completedPages);
                    try { onPageDone(done, totalPages); } catch { /* progress callback must not break ingest */ }
                }
            }
        });

        var pages = await Task.WhenAll(tasks);
        // Deduplicate by timestamp — last writer wins (same as before).
        var merged = new Dictionary<long, (decimal, decimal, decimal, decimal, decimal, decimal)>(pages.Sum(p => p.Count));
        foreach (var p in pages)
            foreach (var (t, o, h, l, c, v, tv) in p) merged[t] = (o, h, l, c, v, tv);

        return merged
            .Where(kv => kv.Key >= startMs && kv.Key <= endMs)
            .OrderBy(kv => kv.Key)
            .Select(kv => (kv.Key, kv.Value.Item1, kv.Value.Item2, kv.Value.Item3,
                           kv.Value.Item4, kv.Value.Item5, kv.Value.Item6))
            .ToList();
    }

    // ── Funding rate history ─────────────────────────────────────────────

    /// <summary>
    /// Fetch funding-rate history for <paramref name="symbol"/> in
    /// [<paramref name="startMs"/>, <paramref name="endMs"/>]. The range is
    /// sliced into windows of <see cref="DatasetConstants.PageLimitFunding"/>
    /// funding events (spacing <paramref name="fundingIntervalMs"/>, default
    /// 8 hours) and each window is fetched independently via
    /// <c>startTime</c>/<c>endTime</c> — no server cursor, no backward paging.
    /// Results are deduplicated by timestamp and returned sorted ascending.
    /// </summary>
    public async Task<IReadOnlyList<(long TimestampMs, decimal Rate)>>
        FetchFundingRatesAsync(
            string symbol, long startMs, long endMs,
            long fundingIntervalMs = 28_800_000L,
            CancellationToken ct = default)
    {
        if (endMs < startMs) return Array.Empty<(long, decimal)>();
        if (fundingIntervalMs <= 0) fundingIntervalMs = 28_800_000L;

        var windowSpan = DatasetConstants.PageLimitFunding * fundingIntervalMs;
        var windows = new List<(long Start, long End)>();
        for (long s = startMs; s <= endMs; s += windowSpan)
        {
            var e = Math.Min(endMs, s + windowSpan - 1);
            windows.Add((s, e));
        }

        using var gate = new SemaphoreSlim(
            DatasetConstants.MaxParallelApiWorkers, DatasetConstants.MaxParallelApiWorkers);
        var tasks = windows.Select(async w =>
        {
            await gate.WaitAsync(ct);
            try
            {
                var url = $"{DatasetConstants.BybitBaseUrl}/v5/market/funding/history"
                        + $"?category=linear&symbol={symbol}"
                        + $"&startTime={w.Start}&endTime={w.End}"
                        + $"&limit={DatasetConstants.PageLimitFunding}";
                using var doc = await GetJsonAsync(url, ct);
                var list = doc.RootElement.GetProperty("result").GetProperty("list");
                var page = new List<(long, decimal)>(
                    list.ValueKind == JsonValueKind.Array ? list.GetArrayLength() : 0);
                if (list.ValueKind != JsonValueKind.Array) return page;
                foreach (var item in list.EnumerateArray())
                {
                    var ts = item.TryGetProperty("fundingRateTimestamp", out var t) ? TryMs(t) : null;
                    var rate = item.TryGetProperty("fundingRate", out var r) ? TryDec(r) : null;
                    if (ts is null || rate is null) continue;
                    page.Add((ts.Value, rate.Value));
                }
                return page;
            }
            finally { gate.Release(); }
        });

        var pages = await Task.WhenAll(tasks);
        var merged = new Dictionary<long, decimal>(pages.Sum(p => p.Count));
        foreach (var p in pages)
            foreach (var (t, v) in p) merged[t] = v;

        return merged.Where(kv => kv.Key >= startMs && kv.Key <= endMs)
                     .OrderBy(kv => kv.Key)
                     .Select(kv => (kv.Key, kv.Value))
                     .ToList();
    }

    // ── Open interest ────────────────────────────────────────────────────

    /// <summary>
    /// Fetch open-interest history for <paramref name="symbol"/> at the given
    /// <paramref name="intervalLabel"/> (e.g. "5min", "1h") in
    /// [<paramref name="startMs"/>, <paramref name="endMs"/>].
    /// The range is sliced into windows of
    /// <see cref="DatasetConstants.PageLimitOpenInterest"/> candles (spacing
    /// <paramref name="intervalMs"/>) and each window is fetched independently
    /// via <c>startTime</c>/<c>endTime</c> — no server cursor. Pages run in
    /// parallel under <see cref="DatasetConstants.MaxParallelApiWorkers"/>
    /// concurrency. Results are deduplicated by timestamp and returned sorted
    /// ascending.
    /// </summary>
    public async Task<IReadOnlyList<(long TimestampMs, decimal Oi)>>
        FetchOpenInterestAsync(
            string symbol, string intervalLabel, long startMs, long endMs,
            long intervalMs,
            CancellationToken ct = default)
    {
        if (endMs < startMs) return Array.Empty<(long, decimal)>();
        if (intervalMs <= 0) throw new ArgumentException("intervalMs must be positive", nameof(intervalMs));

        var windowSpan = DatasetConstants.PageLimitOpenInterest * intervalMs;
        var windows = new List<(long Start, long End)>();
        for (long s = startMs; s <= endMs; s += windowSpan)
        {
            var e = Math.Min(endMs, s + windowSpan - intervalMs);
            windows.Add((s, e));
        }

        using var gate = new SemaphoreSlim(
            DatasetConstants.MaxParallelApiWorkers, DatasetConstants.MaxParallelApiWorkers);
        var tasks = windows.Select(async w =>
        {
            await gate.WaitAsync(ct);
            try
            {
                var url = $"{DatasetConstants.BybitBaseUrl}/v5/market/open-interest"
                        + $"?category=linear&symbol={symbol}&intervalTime={intervalLabel}"
                        + $"&startTime={w.Start}&endTime={w.End}"
                        + $"&limit={DatasetConstants.PageLimitOpenInterest}";
                using var doc = await GetJsonAsync(url, ct);
                var list = doc.RootElement.GetProperty("result").GetProperty("list");
                var page = new List<(long, decimal)>(
                    list.ValueKind == JsonValueKind.Array ? list.GetArrayLength() : 0);
                if (list.ValueKind != JsonValueKind.Array) return page;
                foreach (var item in list.EnumerateArray())
                {
                    var ts = item.TryGetProperty("timestamp", out var t) ? TryMs(t) : null;
                    var oi = item.TryGetProperty("openInterest", out var o) ? TryDec(o) : null;
                    if (ts is null || oi is null) continue;
                    page.Add((ts.Value, oi.Value));
                }
                return page;
            }
            finally { gate.Release(); }
        });

        var pages = await Task.WhenAll(tasks);
        var merged = new Dictionary<long, decimal>(pages.Sum(p => p.Count));
        foreach (var p in pages)
            foreach (var (t, v) in p) merged[t] = v;

        return merged.Where(kv => kv.Key >= startMs && kv.Key <= endMs)
                     .OrderBy(kv => kv.Key)
                     .Select(kv => (kv.Key, kv.Value))
                     .ToList();
    }
}
