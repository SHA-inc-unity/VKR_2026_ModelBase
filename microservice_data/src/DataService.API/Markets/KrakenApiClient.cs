using System.Collections.Concurrent;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using DataService.API.Dataset;

namespace DataService.API.Markets;

public sealed class KrakenApiClient : IMarketDataClient
{
    private const string BaseUrl = "https://api.kraken.com";
    public const string ExchangeName = "kraken";
    public const int MaxBaseCandles = 720;
    private const int MaxKlinePageCandles = 120;
    private const int MaxParallelPageFetches = 2;
    private static readonly TimeSpan RequestDeadline = TimeSpan.FromSeconds(30);
    private const int MaxRequestAttempts = 4;

    private readonly HttpClient _http;
    private readonly ILogger<KrakenApiClient> _log;
    private readonly KrakenRateLimiter _rateLimiter;
    private readonly ConcurrentDictionary<string, string> _pairCache = new(StringComparer.OrdinalIgnoreCase);

    public string Exchange => ExchangeName;

    public KrakenApiClient(HttpClient http, KrakenRateLimiter rateLimiter, ILogger<KrakenApiClient> log)
    {
        _http = http;
        _rateLimiter = rateLimiter;
        _log = log;
    }

    public static (long EffectiveStartMs, long EffectiveEndMs, bool Clipped) ClampRequestedWindow(
        long requestedStartMs,
        long requestedEndMs,
        long stepMs,
        long nowMs)
    {
        var availableStartMs = Math.Max(0L, nowMs - GetReachableLookbackMs(stepMs));
        var effectiveStartMs = Math.Max(requestedStartMs, availableStartMs);
        var effectiveEndMs = Math.Min(requestedEndMs, nowMs);
        return (effectiveStartMs, effectiveEndMs,
            effectiveStartMs != requestedStartMs || effectiveEndMs != requestedEndMs);
    }

    public static string DescribeReachableLookback(long stepMs)
    {
        var (baseIntervalMinutes, aggregateFactor) = ResolveOhlcStrategy(stepMs);
        var targetCandles = Math.Max(1, MaxBaseCandles / aggregateFactor);
        var targetLookback = TimeSpan.FromMilliseconds(targetCandles * stepMs);
        return $"last {targetCandles} candles (~{FormatLookback(targetLookback)}) from {MaxBaseCandles} upstream {baseIntervalMinutes}m candles";
    }

    public async Task<(long LaunchMs, long FundingMs)> FetchInstrumentDetailsAsync(
        string category,
        string symbol,
        CancellationToken ct = default)
    {
        _ = await ResolvePairAsync(symbol, ct);
        return (0L, 0L);
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

        var pair = await ResolvePairAsync(symbol, ct);
        var (baseIntervalMinutes, aggregateFactor) = ResolveOhlcStrategy(stepMs);
        var baseStepMs = baseIntervalMinutes * 60_000L;
        var expectedBaseCandles = CountExpectedCandles(startMs, endMs, baseStepMs);

        if (expectedBaseCandles <= MaxBaseCandles)
        {
            var singlePage = await FetchOhlcWindowAsync(pair, baseIntervalMinutes, startMs, endMs, ct);
            if (HasCompleteCoverage(singlePage, startMs, endMs, expectedBaseCandles))
            {
                if (onPageDone is not null)
                {
                    try { onPageDone(1, 1); } catch { }
                }

                IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)> singleRows =
                    aggregateFactor == 1 ? singlePage : AggregateRows(singlePage, stepMs, aggregateFactor);

                return singleRows
                    .Where(row => row.TimestampMs >= startMs && row.TimestampMs <= endMs)
                    .ToList();
            }

            _log.LogDebug(
                "Kraken single-shot OHLC fetch incomplete for {Symbol}/{Interval}: got {Actual}/{Expected} candles; falling back to paged windows",
                symbol,
                interval,
                singlePage.Count,
                expectedBaseCandles);
        }

        var pageSpanMs = MaxKlinePageCandles * baseStepMs;
        var windows = new List<(long Start, long End)>();
        for (var windowEnd = endMs; windowEnd >= startMs;)
        {
            var windowStart = Math.Max(startMs, windowEnd - pageSpanMs + baseStepMs);
            windows.Add((windowStart, windowEnd));
            if (windowStart == startMs) break;
            windowEnd = windowStart - baseStepMs;
        }
        windows.Reverse();

        var totalPages = windows.Count;
        var completedPages = 0;
        var requestedDegree = maxParallel > 0 ? maxParallel : DatasetConstants.MaxParallelApiWorkers;
        var degree = Math.Min(requestedDegree, MaxParallelPageFetches);
        using var gate = new SemaphoreSlim(degree, degree);
        var tasks = windows.Select(async w =>
        {
            await gate.WaitAsync(ct);
            try
            {
                return await FetchOhlcWindowAsync(pair, baseIntervalMinutes, w.Start, w.End, ct);
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
        var merged = new Dictionary<long, (decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>(pages.Sum(p => p.Count));
        foreach (var page in pages)
        {
            foreach (var (timestampMs, open, high, low, close, volume, turnover) in page)
            {
                merged[timestampMs] = (open, high, low, close, volume, turnover);
            }
        }

        var baseRows = merged
            .OrderBy(kv => kv.Key)
            .Select(kv => (kv.Key, kv.Value.Open, kv.Value.High, kv.Value.Low, kv.Value.Close, kv.Value.Volume, kv.Value.Turnover))
            .ToList();

        IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)> rows =
            aggregateFactor == 1 ? baseRows : AggregateRows(baseRows, stepMs, aggregateFactor);

        return rows
            .Where(row => row.TimestampMs >= startMs && row.TimestampMs <= endMs)
            .ToList();
    }

    public Task<IReadOnlyList<(long TimestampMs, decimal Rate)>> FetchFundingRatesAsync(
        string symbol,
        long startMs,
        long endMs,
        long fundingIntervalMs = 28_800_000L,
        CancellationToken ct = default)
    {
        IReadOnlyList<(long TimestampMs, decimal Rate)> empty = Array.Empty<(long, decimal)>();
        return Task.FromResult(empty);
    }

    public Task<IReadOnlyList<(long TimestampMs, decimal Oi)>> FetchOpenInterestAsync(
        string symbol,
        string intervalLabel,
        long startMs,
        long endMs,
        long intervalMs,
        CancellationToken ct = default,
        Action<int, int>? onPageDone = null)
    {
        IReadOnlyList<(long TimestampMs, decimal Oi)> empty = Array.Empty<(long, decimal)>();
        return Task.FromResult(empty);
    }

    private async Task<List<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>>
        FetchOhlcWindowAsync(
            string pair,
            int baseIntervalMinutes,
            long startMs,
            long endMs,
            CancellationToken ct)
    {
        var sinceSeconds = Math.Max(0L, startMs / 1000L);
        var url = $"{BaseUrl}/0/public/OHLC?pair={Uri.EscapeDataString(pair)}&interval={baseIntervalMinutes}&since={sinceSeconds}";

        using var doc = await GetJsonAsync(url, ct);
        var root = doc.RootElement;
        if (!root.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object)
            return new List<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>();

        JsonElement? data = null;
        foreach (var prop in result.EnumerateObject())
        {
            if (string.Equals(prop.Name, "last", StringComparison.OrdinalIgnoreCase)) continue;
            data = prop.Value;
            break;
        }
        if (data is null || data.Value.ValueKind != JsonValueKind.Array)
            return new List<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>();

        var page = new List<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>(data.Value.GetArrayLength());
        foreach (var item in data.Value.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Array || item.GetArrayLength() < 8) continue;
            var tsSec = TryLong(item[0]);
            var open = TryDec(item[1]);
            var high = TryDec(item[2]);
            var low = TryDec(item[3]);
            var close = TryDec(item[4]);
            var vwap = TryDec(item[5]);
            var volume = TryDec(item[6]);
            if (tsSec is null || open is null || high is null || low is null || close is null || volume is null) continue;
            var tsMs = tsSec.Value * 1000L;
            if (tsMs < startMs || tsMs > endMs) continue;
            var turnover = (vwap ?? close.Value) * volume.Value;
            page.Add((tsMs, open.Value, high.Value, low.Value, close.Value, volume.Value, turnover));
        }

        return page
            .OrderBy(row => row.TimestampMs)
            .ToList();
    }

    private async Task<string> ResolvePairAsync(string symbol, CancellationToken ct)
    {
        var normalized = symbol.Trim().ToUpperInvariant();
        if (_pairCache.TryGetValue(normalized, out var cached)) return cached;

        foreach (var candidate in BuildPairCandidates(normalized))
        {
            using var doc = await GetJsonAsync(
                $"{BaseUrl}/0/public/AssetPairs?pair={Uri.EscapeDataString(candidate)}",
                ct);
            if (!doc.RootElement.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object)
            {
                continue;
            }

            foreach (var prop in result.EnumerateObject())
            {
                var altname = prop.Value.TryGetProperty("altname", out var alt) ? alt.GetString() : null;
                var wsname = prop.Value.TryGetProperty("wsname", out var ws) ? ws.GetString() : null;
                if (!string.Equals(prop.Name, candidate, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(altname, candidate, StringComparison.OrdinalIgnoreCase)
                    && !string.Equals(wsname, candidate, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                var resolved = altname ?? prop.Name;
                _pairCache.TryAdd(normalized, resolved);
                return resolved;
            }
        }

        throw new ArgumentException($"unsupported Kraken symbol: {symbol}");
    }

    private async Task<JsonDocument> GetJsonAsync(string url, CancellationToken ct)
    {
        Exception? last = null;
        for (int attempt = 0; attempt < MaxRequestAttempts; attempt++)
        {
            HttpResponseMessage? response = null;
            using var requestCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            requestCts.CancelAfter(RequestDeadline);
            try
            {
                using var rateLease = await _rateLimiter.AcquireAsync(ct);
                using var request = new HttpRequestMessage(HttpMethod.Get, url)
                {
                    Version = HttpVersion.Version11,
                    VersionPolicy = HttpVersionPolicy.RequestVersionOrLower,
                };
                request.Headers.ConnectionClose = true;

                response = await _http.SendAsync(request, HttpCompletionOption.ResponseContentRead, requestCts.Token);
                var body = await response.Content.ReadAsStringAsync(requestCts.Token);

                if ((int)response.StatusCode == 429 || (int)response.StatusCode >= 500)
                {
                    last = BuildHttpException(url, response.StatusCode, body);
                    var delay = GetHttpRetryDelay(response, attempt);
                    if ((int)response.StatusCode == 429)
                    {
                        _rateLimiter.Penalize(delay);
                    }
                    response.Dispose();
                    response = null;
                    await Task.Delay(delay, ct);
                    continue;
                }

                if (!response.IsSuccessStatusCode)
                    throw BuildHttpException(url, response.StatusCode, body);

                var doc = JsonDocument.Parse(body);
                if (TryExtractKrakenErrors(doc.RootElement, out var errors))
                {
                    if (TryGetKrakenRetryDelay(errors, attempt, out var delay))
                    {
                        last = new InvalidOperationException($"Kraken API error for {url}: {errors}");
                        _rateLimiter.Penalize(delay);
                        doc.Dispose();
                        await Task.Delay(delay, ct);
                        continue;
                    }

                    doc.Dispose();
                    throw new InvalidOperationException($"Kraken API error for {url}: {errors}");
                }

                return doc;
            }
            catch (OperationCanceledException ex) when (!ct.IsCancellationRequested)
            {
                last = ex;
                _log.LogWarning(ex,
                    "Kraken GET {Url} exceeded {DeadlineSeconds}s (attempt {Attempt}/{Max})",
                    url, RequestDeadline.TotalSeconds, attempt + 1, MaxRequestAttempts);
                await Task.Delay(TimeSpan.FromMilliseconds(Math.Pow(2, attempt) * 1000), ct);
            }
            catch (Exception ex) when (IsTransient(ex))
            {
                last = ex;
                _log.LogWarning(ex, "Transient Kraken error on GET {Url} (attempt {Attempt}/{Max})",
                    url, attempt + 1, MaxRequestAttempts);
                await Task.Delay(TimeSpan.FromMilliseconds(Math.Pow(2, attempt) * 1000 + Random.Shared.Next(0, 500)), ct);
            }
            finally
            {
                response?.Dispose();
            }
        }

        throw new HttpRequestException($"GET {url} failed after {MaxRequestAttempts} attempts", last);
    }

    private static TimeSpan GetHttpRetryDelay(HttpResponseMessage response, int attempt)
    {
        if (response.Headers.RetryAfter?.Delta is { } delta && delta > TimeSpan.Zero)
        {
            return delta;
        }

        return TimeSpan.FromSeconds(Math.Min(12, Math.Pow(2, attempt + 1)) + Random.Shared.NextDouble());
    }

    private static bool TryGetKrakenRetryDelay(string errors, int attempt, out TimeSpan delay)
    {
        delay = TimeSpan.Zero;
        if (string.IsNullOrWhiteSpace(errors)) return false;

        if (TryParseThrottleUntil(errors, out var untilUtc))
        {
            delay = untilUtc - DateTimeOffset.UtcNow;
            if (delay < TimeSpan.FromSeconds(1)) delay = TimeSpan.FromSeconds(1);
            return true;
        }

        if (errors.Contains("Too many requests", StringComparison.OrdinalIgnoreCase)
            || errors.Contains("Rate limit exceeded", StringComparison.OrdinalIgnoreCase)
            || errors.Contains("Throttled", StringComparison.OrdinalIgnoreCase))
        {
            delay = TimeSpan.FromSeconds(Math.Min(20, 3 * (attempt + 1)) + Random.Shared.NextDouble());
            return true;
        }

        return false;
    }

    private static bool TryParseThrottleUntil(string errors, out DateTimeOffset untilUtc)
    {
        untilUtc = default;
        const string marker = "Throttled:";
        var index = errors.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
        if (index < 0) return false;

        var suffix = errors[(index + marker.Length)..].Trim();
        var digits = new string(suffix.TakeWhile(char.IsDigit).ToArray());
        if (!long.TryParse(digits, out var unixSeconds)) return false;

        untilUtc = DateTimeOffset.FromUnixTimeSeconds(unixSeconds).AddSeconds(1);
        return true;
    }

    private static bool IsTransient(Exception ex) => ex switch
    {
        HttpRequestException httpEx when httpEx.StatusCode is null => true,
        HttpRequestException httpEx when httpEx.StatusCode == HttpStatusCode.TooManyRequests => true,
        HttpRequestException httpEx when httpEx.StatusCode is >= HttpStatusCode.InternalServerError => true,
        TaskCanceledException { InnerException: TimeoutException } => true,
        System.Net.Sockets.SocketException => true,
        IOException { InnerException: System.Net.Sockets.SocketException } => true,
        _ => false,
    };

    private static bool TryExtractKrakenErrors(JsonElement root, out string errors)
    {
        errors = string.Empty;
        if (!root.TryGetProperty("error", out var errorNode) || errorNode.ValueKind != JsonValueKind.Array)
            return false;

        var parts = errorNode
            .EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.String)
            .Select(item => item.GetString())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .Cast<string>()
            .ToArray();

        if (parts.Length == 0)
            return false;

        errors = string.Join(", ", parts);
        return true;
    }

    private static HttpRequestException BuildHttpException(string url, HttpStatusCode statusCode, string body)
    {
        var snippet = string.IsNullOrWhiteSpace(body)
            ? string.Empty
            : $": {body[..Math.Min(body.Length, 240)].Replace('\r', ' ').Replace('\n', ' ')}";
        return new HttpRequestException($"GET {url} returned {(int)statusCode} ({statusCode}){snippet}", null, statusCode);
    }

    private static string[] BuildPairCandidates(string symbol)
    {
        var candidates = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { symbol };
        if (!symbol.EndsWith("USDT", StringComparison.OrdinalIgnoreCase)) return candidates.ToArray();

        var baseAsset = symbol[..^4];
        var mappedBase = baseAsset.Equals("BTC", StringComparison.OrdinalIgnoreCase) ? "XBT" : baseAsset;
        candidates.Add($"{baseAsset}/USDT");
        candidates.Add($"{mappedBase}USDT");
        candidates.Add($"{mappedBase}/USDT");
        return candidates.ToArray();
    }

    private static long GetReachableLookbackMs(long stepMs)
    {
        var (baseIntervalMinutes, _) = ResolveOhlcStrategy(stepMs);
        return MaxBaseCandles * baseIntervalMinutes * 60_000L;
    }

    private static int CountExpectedCandles(long startMs, long endMs, long baseStepMs)
    {
        if (endMs < startMs || baseStepMs <= 0) return 0;
        return (int)(((endMs - startMs) / baseStepMs) + 1);
    }

    private static bool HasCompleteCoverage(
        IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)> rows,
        long startMs,
        long endMs,
        int expectedCandles)
    {
        if (rows.Count == 0 || expectedCandles <= 0) return false;
        return rows.Count >= expectedCandles
            && rows[0].TimestampMs == startMs
            && rows[^1].TimestampMs == endMs;
    }

    private static (int BaseIntervalMinutes, int AggregateFactor) ResolveOhlcStrategy(long stepMs) => stepMs switch
    {
        60_000 => (1, 1),
        180_000 => (1, 3),
        300_000 => (5, 1),
        900_000 => (15, 1),
        1_800_000 => (30, 1),
        3_600_000 => (60, 1),
        7_200_000 => (60, 2),
        14_400_000 => (240, 1),
        21_600_000 => (60, 6),
        43_200_000 => (60, 12),
        86_400_000 => (1440, 1),
        _ => throw new ArgumentException($"unsupported Kraken step_ms: {stepMs}", nameof(stepMs)),
    };

    private static string FormatLookback(TimeSpan lookback)
    {
        if (lookback.TotalDays >= 1)
            return $"{Math.Round(lookback.TotalDays, 1):0.#}d";
        if (lookback.TotalHours >= 1)
            return $"{Math.Round(lookback.TotalHours, 1):0.#}h";
        return $"{Math.Round(lookback.TotalMinutes, 1):0.#}m";
    }

    private static IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>
        AggregateRows(
            IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)> rows,
            long stepMs,
            int aggregateFactor)
    {
        var ordered = rows.OrderBy(x => x.TimestampMs).ToList();
        var result = new List<(long, decimal, decimal, decimal, decimal, decimal, decimal)>();

        foreach (var group in ordered.GroupBy(x => x.TimestampMs - x.TimestampMs % stepMs))
        {
            var bucket = group.OrderBy(x => x.TimestampMs).ToList();
            if (bucket.Count < aggregateFactor) continue;
            result.Add((
                group.Key,
                bucket[0].Open,
                bucket.Max(x => x.High),
                bucket.Min(x => x.Low),
                bucket[^1].Close,
                bucket.Sum(x => x.Volume),
                bucket.Sum(x => x.Turnover)));
        }

        return result;
    }

    private static decimal? TryDec(in JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Number && e.TryGetDecimal(out var n)) return n;
        if (e.ValueKind == JsonValueKind.String && decimal.TryParse(
                e.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var s)) return s;
        return null;
    }

    private static long? TryLong(in JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Number && e.TryGetInt64(out var n)) return n;
        if (e.ValueKind == JsonValueKind.String && long.TryParse(e.GetString(), out var s)) return s;
        return null;
    }
}