using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using DataService.API.Dataset;

namespace DataService.API.Markets;

public sealed class KrakenApiClient : IMarketDataClient
{
    private const string BaseUrl = "https://api.kraken.com";
    public const string ExchangeName = "kraken";
    public const int MaxBaseCandles = 720;
    private static readonly TimeSpan RequestDeadline = TimeSpan.FromSeconds(30);
    private const int MaxRequestAttempts = 2;

    private readonly HttpClient _http;
    private readonly ILogger<KrakenApiClient> _log;
    private readonly ConcurrentDictionary<string, string> _pairCache = new(StringComparer.OrdinalIgnoreCase);

    public string Exchange => ExchangeName;

    public KrakenApiClient(HttpClient http, ILogger<KrakenApiClient> log)
    {
        _http = http;
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
        var sinceSeconds = Math.Max(0L, startMs / 1000L);
        var url = $"{BaseUrl}/0/public/OHLC?pair={Uri.EscapeDataString(pair)}&interval={baseIntervalMinutes}&since={sinceSeconds}";

        using var doc = await GetJsonAsync(url, ct);
        var root = doc.RootElement;
        var candles = new List<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>();
        if (!root.TryGetProperty("result", out var result) || result.ValueKind != JsonValueKind.Object)
            return candles;

        JsonElement? data = null;
        foreach (var prop in result.EnumerateObject())
        {
            if (string.Equals(prop.Name, "last", StringComparison.OrdinalIgnoreCase)) continue;
            data = prop.Value;
            break;
        }
        if (data is null || data.Value.ValueKind != JsonValueKind.Array) return candles;

        var baseRows = new List<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>(data.Value.GetArrayLength());
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
            baseRows.Add((tsMs, open.Value, high.Value, low.Value, close.Value, volume.Value, turnover));
        }

        IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)> rows =
            aggregateFactor == 1 ? baseRows.OrderBy(x => x.TimestampMs).ToList() : AggregateRows(baseRows, stepMs, aggregateFactor);

        if (onPageDone is not null)
        {
            try { onPageDone(1, 1); } catch { }
        }

        return rows;
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
        CancellationToken ct = default)
    {
        IReadOnlyList<(long TimestampMs, decimal Oi)> empty = Array.Empty<(long, decimal)>();
        return Task.FromResult(empty);
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
                response = await _http.GetAsync(url, HttpCompletionOption.ResponseContentRead, requestCts.Token);
                if ((int)response.StatusCode == 429 || (int)response.StatusCode >= 500)
                {
                    last = new HttpRequestException($"HTTP {(int)response.StatusCode}");
                    response.Dispose();
                    response = null;
                    await Task.Delay(TimeSpan.FromSeconds(Math.Pow(2, attempt) + Random.Shared.NextDouble()), ct);
                    continue;
                }

                response.EnsureSuccessStatusCode();
                return JsonDocument.Parse(await response.Content.ReadAsStringAsync(requestCts.Token));
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

    private static bool IsTransient(Exception ex) => ex switch
    {
        HttpRequestException => true,
        TaskCanceledException { InnerException: TimeoutException } => true,
        System.Net.Sockets.SocketException => true,
        IOException { InnerException: System.Net.Sockets.SocketException } => true,
        _ => false,
    };

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