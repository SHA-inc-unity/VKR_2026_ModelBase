using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using DataService.API.Dataset;

namespace DataService.API.Markets;

public sealed class BinanceApiClient : IMarketDataClient
{
    private const string BaseUrl = "https://fapi.binance.com";

    private readonly HttpClient _http;
    private readonly ILogger<BinanceApiClient> _log;
    private readonly ConcurrentDictionary<string, (long LaunchMs, long FundingMs)> _instrumentCache = new();

    public string Exchange => "binance";

    public BinanceApiClient(HttpClient http, ILogger<BinanceApiClient> log)
    {
        _http = http;
        _log = log;
    }

    public async Task<(long LaunchMs, long FundingMs)> FetchInstrumentDetailsAsync(
        string category,
        string symbol,
        CancellationToken ct = default)
    {
        var normalizedSymbol = symbol.Trim().ToUpperInvariant();
        if (_instrumentCache.TryGetValue(normalizedSymbol, out var cached)) return cached;

        using var doc = await GetJsonAsync($"{BaseUrl}/fapi/v1/exchangeInfo", ct);
        if (doc.RootElement.TryGetProperty("symbols", out var symbols)
            && symbols.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in symbols.EnumerateArray())
            {
                var name = item.TryGetProperty("symbol", out var s) ? s.GetString() : null;
                if (!string.Equals(name, normalizedSymbol, StringComparison.OrdinalIgnoreCase)) continue;
                var launchMs = item.TryGetProperty("onboardDate", out var onboard)
                    ? TryMs(onboard) ?? 0L
                    : 0L;
                var result = (launchMs, 28_800_000L);
                _instrumentCache.TryAdd(normalizedSymbol, result);
                return result;
            }
        }

        var fallback = (0L, 28_800_000L);
        _instrumentCache.TryAdd(normalizedSymbol, fallback);
        return fallback;
    }

    public async Task<IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>>
        FetchKlinesAsync(
            string symbol,
            string interval,
            long startMs,
            long endMs,
            long stepMs,
            int maxParallel = 0,
            CancellationToken ct = default,
            Action<int, int>? onPageDone = null)
    {
        if (stepMs <= 0) throw new ArgumentException("stepMs must be positive", nameof(stepMs));
        if (endMs < startMs) return Array.Empty<(long, decimal, decimal, decimal, decimal, decimal, decimal)>();

        var binanceInterval = ToBinanceInterval(stepMs);
        var pageSpan = 1_500L * stepMs;
        var windows = new List<(long Start, long End)>();
        for (long windowStart = startMs; windowStart <= endMs; windowStart += pageSpan)
        {
            var windowEnd = Math.Min(endMs, windowStart + pageSpan - stepMs);
            windows.Add((windowStart, windowEnd));
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
                var url = $"{BaseUrl}/fapi/v1/klines"
                        + $"?symbol={Uri.EscapeDataString(symbol)}&interval={Uri.EscapeDataString(binanceInterval)}"
                        + $"&startTime={w.Start}&endTime={w.End}&limit=1500";
                using var doc = await GetJsonAsync(url, ct);
                var root = doc.RootElement;
                var page = new List<(long, decimal, decimal, decimal, decimal, decimal, decimal)>(
                    root.ValueKind == JsonValueKind.Array ? root.GetArrayLength() : 0);
                if (root.ValueKind != JsonValueKind.Array) return page;

                foreach (var item in root.EnumerateArray())
                {
                    if (item.ValueKind != JsonValueKind.Array || item.GetArrayLength() < 8) continue;
                    var ts = TryMs(item[0]);
                    var open = TryDec(item[1]);
                    var high = TryDec(item[2]);
                    var low = TryDec(item[3]);
                    var close = TryDec(item[4]);
                    var volume = TryDec(item[5]);
                    var turnover = TryDec(item[7]);
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
                    try { onPageDone(done, totalPages); } catch { }
                }
            }
        });

        var pages = await Task.WhenAll(tasks);
        var merged = new Dictionary<long, (decimal, decimal, decimal, decimal, decimal, decimal)>(pages.Sum(p => p.Count));
        foreach (var page in pages)
            foreach (var (t, o, h, l, c, v, tv) in page)
                merged[t] = (o, h, l, c, v, tv);

        return merged
            .Where(kv => kv.Key >= startMs && kv.Key <= endMs)
            .OrderBy(kv => kv.Key)
            .Select(kv => (kv.Key, kv.Value.Item1, kv.Value.Item2, kv.Value.Item3, kv.Value.Item4, kv.Value.Item5, kv.Value.Item6))
            .ToList();
    }

    public async Task<IReadOnlyList<(long TimestampMs, decimal Rate)>> FetchFundingRatesAsync(
        string symbol,
        long startMs,
        long endMs,
        long fundingIntervalMs = 28_800_000L,
        CancellationToken ct = default)
    {
        if (endMs < startMs) return Array.Empty<(long, decimal)>();
        if (fundingIntervalMs <= 0) fundingIntervalMs = 28_800_000L;

        var windowSpan = 1_000L * fundingIntervalMs;
        var windows = new List<(long Start, long End)>();
        for (long windowStart = startMs; windowStart <= endMs; windowStart += windowSpan)
        {
            var windowEnd = Math.Min(endMs, windowStart + windowSpan - 1);
            windows.Add((windowStart, windowEnd));
        }

        using var gate = new SemaphoreSlim(DatasetConstants.MaxParallelApiWorkers, DatasetConstants.MaxParallelApiWorkers);
        var tasks = windows.Select(async w =>
        {
            await gate.WaitAsync(ct);
            try
            {
                var url = $"{BaseUrl}/fapi/v1/fundingRate"
                        + $"?symbol={Uri.EscapeDataString(symbol)}&startTime={w.Start}&endTime={w.End}&limit=1000";
                using var doc = await GetJsonAsync(url, ct);
                var root = doc.RootElement;
                var page = new List<(long, decimal)>(root.ValueKind == JsonValueKind.Array ? root.GetArrayLength() : 0);
                if (root.ValueKind != JsonValueKind.Array) return page;

                foreach (var item in root.EnumerateArray())
                {
                    var ts = item.TryGetProperty("fundingTime", out var t) ? TryMs(t) : null;
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
        foreach (var page in pages)
            foreach (var (t, v) in page)
                merged[t] = v;

        return merged
            .Where(kv => kv.Key >= startMs && kv.Key <= endMs)
            .OrderBy(kv => kv.Key)
            .Select(kv => (kv.Key, kv.Value))
            .ToList();
    }

    public async Task<IReadOnlyList<(long TimestampMs, decimal Oi)>> FetchOpenInterestAsync(
        string symbol,
        string intervalLabel,
        long startMs,
        long endMs,
        long intervalMs,
        CancellationToken ct = default)
    {
        if (endMs < startMs) return Array.Empty<(long, decimal)>();
        if (intervalMs <= 0) throw new ArgumentException("intervalMs must be positive", nameof(intervalMs));

        var period = ToBinanceOpenInterestPeriod(intervalLabel, intervalMs);
        var windowSpan = 500L * intervalMs;
        var windows = new List<(long Start, long End)>();
        for (long windowStart = startMs; windowStart <= endMs; windowStart += windowSpan)
        {
            var windowEnd = Math.Min(endMs, windowStart + windowSpan - intervalMs);
            windows.Add((windowStart, windowEnd));
        }

        using var gate = new SemaphoreSlim(DatasetConstants.MaxParallelApiWorkers, DatasetConstants.MaxParallelApiWorkers);
        var tasks = windows.Select(async w =>
        {
            await gate.WaitAsync(ct);
            try
            {
                var url = $"{BaseUrl}/futures/data/openInterestHist"
                        + $"?symbol={Uri.EscapeDataString(symbol)}&period={Uri.EscapeDataString(period)}"
                        + $"&startTime={w.Start}&endTime={w.End}&limit=500";
                using var doc = await GetJsonAsync(url, ct);
                var root = doc.RootElement;
                var page = new List<(long, decimal)>(root.ValueKind == JsonValueKind.Array ? root.GetArrayLength() : 0);
                if (root.ValueKind != JsonValueKind.Array) return page;

                foreach (var item in root.EnumerateArray())
                {
                    var ts = item.TryGetProperty("timestamp", out var t) ? TryMs(t) : null;
                    var oi = item.TryGetProperty("sumOpenInterest", out var o) ? TryDec(o) : null;
                    if (ts is null || oi is null) continue;
                    page.Add((ts.Value, oi.Value));
                }

                return page;
            }
            finally { gate.Release(); }
        });

        var pages = await Task.WhenAll(tasks);
        var merged = new Dictionary<long, decimal>(pages.Sum(p => p.Count));
        foreach (var page in pages)
            foreach (var (t, v) in page)
                merged[t] = v;

        return merged
            .Where(kv => kv.Key >= startMs && kv.Key <= endMs)
            .OrderBy(kv => kv.Key)
            .Select(kv => (kv.Key, kv.Value))
            .ToList();
    }

    private async Task<JsonDocument> GetJsonAsync(string url, CancellationToken ct)
    {
        Exception? last = null;
        for (int attempt = 0; attempt < DatasetConstants.MaxRetries; attempt++)
        {
            HttpResponseMessage? response = null;
            try
            {
                response = await _http.GetAsync(url, HttpCompletionOption.ResponseContentRead, ct);
                if ((int)response.StatusCode == 429 || (int)response.StatusCode >= 500)
                {
                    last = new HttpRequestException($"HTTP {(int)response.StatusCode}");
                    var wait = TimeSpan.FromSeconds(Math.Pow(2, attempt) + Random.Shared.NextDouble());
                    _log.LogWarning("Binance HTTP {Code} on GET {Url} (attempt {Attempt}/{Max}); wait {Wait:F1}s",
                        (int)response.StatusCode, url, attempt + 1, DatasetConstants.MaxRetries, wait.TotalSeconds);
                    response.Dispose();
                    response = null;
                    await Task.Delay(wait, ct);
                    continue;
                }

                response.EnsureSuccessStatusCode();
                return JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct));
            }
            catch (OperationCanceledException ex) when (!ct.IsCancellationRequested)
            {
                last = ex;
                await Task.Delay(TimeSpan.FromMilliseconds(Math.Pow(2, attempt) * 1000), ct);
            }
            catch (Exception ex) when (IsTransient(ex))
            {
                last = ex;
                _log.LogWarning(ex, "Transient Binance error on GET {Url} (attempt {Attempt}/{Max})",
                    url, attempt + 1, DatasetConstants.MaxRetries);
                await Task.Delay(TimeSpan.FromMilliseconds(Math.Pow(2, attempt) * 1000 + Random.Shared.Next(0, 500)), ct);
            }
            finally
            {
                response?.Dispose();
            }
        }

        throw new HttpRequestException($"GET {url} failed after {DatasetConstants.MaxRetries} attempts", last);
    }

    private static bool IsTransient(Exception ex) => ex switch
    {
        HttpRequestException => true,
        TaskCanceledException { InnerException: TimeoutException } => true,
        System.Net.Sockets.SocketException => true,
        IOException { InnerException: System.Net.Sockets.SocketException } => true,
        _ => false,
    };

    private static decimal? TryDec(in JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Number && e.TryGetDecimal(out var n)) return n;
        if (e.ValueKind == JsonValueKind.String && decimal.TryParse(
                e.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var s)) return s;
        return null;
    }

    private static long? TryMs(in JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Number && e.TryGetInt64(out var n)) return n;
        if (e.ValueKind == JsonValueKind.String && long.TryParse(e.GetString(), out var s)) return s;
        return null;
    }

    private static string ToBinanceInterval(long stepMs) => stepMs switch
    {
        60_000 => "1m",
        180_000 => "3m",
        300_000 => "5m",
        900_000 => "15m",
        1_800_000 => "30m",
        3_600_000 => "1h",
        7_200_000 => "2h",
        14_400_000 => "4h",
        21_600_000 => "6h",
        43_200_000 => "12h",
        86_400_000 => "1d",
        _ => throw new ArgumentException($"unsupported Binance step_ms: {stepMs}", nameof(stepMs)),
    };

    private static string ToBinanceOpenInterestPeriod(string intervalLabel, long intervalMs)
    {
        var normalized = intervalLabel.Trim().ToLowerInvariant();
        return normalized switch
        {
            "5min" => "5m",
            "15min" => "15m",
            "30min" => "30m",
            "1h" => "1h",
            "2h" => "2h",
            "4h" => "4h",
            "6h" => "6h",
            "12h" => "12h",
            "1d" => "1d",
            _ => intervalMs switch
            {
                300_000 => "5m",
                900_000 => "15m",
                1_800_000 => "30m",
                3_600_000 => "1h",
                7_200_000 => "2h",
                14_400_000 => "4h",
                21_600_000 => "6h",
                43_200_000 => "12h",
                86_400_000 => "1d",
                _ => throw new ArgumentException($"unsupported Binance open-interest interval: {intervalLabel}/{intervalMs}"),
            },
        };
    }
}