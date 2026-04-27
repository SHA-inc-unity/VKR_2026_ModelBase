using System.Text.Json;
using DataService.API.Bybit;
using DataService.API.Database;
using DataService.API.Dataset;

namespace DataService.API.Jobs;

/// <summary>
/// Phase C job handlers. Each handler parses its params from
/// <see cref="JobContext.Job"/>.<c>ParamsJson</c> and reports stage-based
/// progress through the context. Heavy work delegates to
/// <see cref="DatasetRepository"/>.
///
/// These handlers are intentionally compact: the legacy sync KafkaConsumer
/// handlers (HandleIngestAsync etc.) remain in place for now, so callers
/// have a choice. The redesigned admin UI uses jobs.start; ops scripts and
/// compute_features-on-ingest still go through the legacy path until
/// Phase G migrates them.
/// </summary>
internal static class JobHandlerHelpers
{
    public static JsonElement Params(this JobContext ctx)
    {
        try { return JsonDocument.Parse(string.IsNullOrWhiteSpace(ctx.Job.ParamsJson) ? "{}" : ctx.Job.ParamsJson).RootElement.Clone(); }
        catch { return JsonDocument.Parse("{}").RootElement.Clone(); }
    }

    public static string? S(this JsonElement p, string n) =>
        p.ValueKind == JsonValueKind.Object && p.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() : null;

    public static long? L(this JsonElement p, string n)
    {
        if (p.ValueKind != JsonValueKind.Object || !p.TryGetProperty(n, out var v)) return null;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var i)) return i;
        if (v.ValueKind == JsonValueKind.String && long.TryParse(v.GetString(), out var s)) return s;
        return null;
    }

    public static bool B(this JsonElement p, string n, bool def = false)
    {
        if (p.ValueKind != JsonValueKind.Object || !p.TryGetProperty(n, out var v)) return def;
        return v.ValueKind switch
        {
            JsonValueKind.True  => true,
            JsonValueKind.False => false,
            _ => def,
        };
    }
}

public sealed class IngestJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.Ingest;
    private readonly DatasetRepository _repo;
    private readonly BybitApiClient _bybit;

    public IngestJobHandler(DatasetRepository repo, BybitApiClient bybit)
    {
        _repo = repo;
        _bybit = bybit;
    }

    public async Task ExecuteAsync(JobContext ctx)
    {
        var p = ctx.Params();
        var symbol    = p.S("symbol")    ?? throw new ArgumentException("symbol required");
        var timeframe = p.S("timeframe") ?? throw new ArgumentException("timeframe required");
        var startMs   = p.L("start_ms")  ?? throw new ArgumentException("start_ms required");
        var endMs     = p.L("end_ms")    ?? throw new ArgumentException("end_ms required");

        var (key, interval, stepMs) = DatasetCore.NormalizeTimeframe(timeframe);
        var (s, e) = DatasetCore.NormalizeWindow(startMs, endMs, stepMs);
        var table = DatasetCore.MakeTableName(symbol, key);

        // ── prepare ────────────────────────────────────────────
        var stagePrep = await ctx.StartStageAsync("prepare");
        await ctx.ReportAsync("prepare", 1, $"table={table}");
        await _repo.CreateTableIfNotExistsAsync(table, ctx.CancellationToken);
        var missing = await _repo.FindMissingTimestampsAsync(table, s, e, stepMs, ctx.CancellationToken);
        await ctx.EndStageAsync(stagePrep, missing.Count);

        if (missing.Count == 0)
        {
            await ctx.ReportAsync("done", 100, $"no missing rows in {table}", total: 0, completed: 0);
            return;
        }

        const int rsiPeriod = 14;
        var warmup = Math.Max(DatasetConstants.DefaultWarmupCandles, rsiPeriod * 2);
        var fetchStart = s - warmup * stepMs;
        var (oiLabel, oiIntervalMs) = DatasetCore.ChooseOpenInterestInterval(stepMs);
        const long fundingMs = 28_800_000L;

        // ── fetch (klines + funding + OI in parallel) ──────────
        var stageFetch = await ctx.StartStageAsync("fetch");
        await ctx.ReportAsync("fetch_klines", 5, $"missing={missing.Count}, fetching market data", total: missing.Count);

        var klineT = _bybit.FetchKlinesAsync(
            symbol.ToUpperInvariant(), interval, fetchStart, e, stepMs, 0, ctx.CancellationToken,
            onPageDone: (done, total) =>
            {
                if (total > 0 && done % 5 == 0)
                {
                    var pct = (int)Math.Min(40, 5 + (long)done * 35 / total);
                    _ = ctx.ReportAsync("fetch_klines", pct, $"{done}/{total} pages", total: missing.Count);
                }
            });
        var fundingT = _bybit.FetchFundingRatesAsync(
            symbol.ToUpperInvariant(), missing[0] - fundingMs, missing[^1], fundingMs, ctx.CancellationToken);
        var oiT = _bybit.FetchOpenInterestAsync(
            symbol.ToUpperInvariant(), oiLabel, missing[0] - oiIntervalMs, missing[^1], oiIntervalMs, ctx.CancellationToken);

        var klines  = await klineT;
        var funding = await fundingT;
        var oi      = await oiT;
        await ctx.EndStageAsync(stageFetch, klines.Count);

        if (await ctx.IsCancelRequestedAsync()) throw new OperationCanceledException();
        await ctx.ReportAsync("compute_rsi", 50, $"klines={klines.Count}, funding={funding.Count}, oi={oi.Count}");

        // RSI compute reuses the legacy helper exposed on KafkaConsumerService;
        // we re-implement the simpler version here to avoid coupling.
        var rsiByTs = ComputeWilderRsi(klines.Select(k => (k.TimestampMs, k.Close)).ToList(), rsiPeriod);

        var fundingFf = BuildForwardFill(funding);
        var oiFf      = BuildForwardFill(oi);
        var klinesByTs = klines.ToDictionary(k => k.TimestampMs, k => k);

        var rows = new List<DatasetRepository.MarketRow>(missing.Count);
        foreach (var ts in missing)
        {
            if (!klinesByTs.TryGetValue(ts, out var k)) continue;
            rows.Add(new DatasetRepository.MarketRow(
                TimestampMs:  ts, Symbol: symbol.ToUpperInvariant(), Exchange: "bybit", Timeframe: key,
                OpenPrice: k.Open, HighPrice: k.High, LowPrice: k.Low, ClosePrice: k.Close,
                Volume: k.Volume, Turnover: k.Turnover,
                FundingRate: LookupForwardFill(fundingFf, ts),
                OpenInterest: LookupForwardFill(oiFf, ts),
                Rsi: rsiByTs.TryGetValue(ts, out var r) ? r : (decimal?)null));
        }

        // ── upsert ─────────────────────────────────────────────
        var stageUp = await ctx.StartStageAsync("upsert");
        await ctx.ReportAsync("upsert", 70, $"writing {rows.Count} rows");
        var written = await _repo.BulkUpsertAsync(table, rows, ctx.CancellationToken);
        await ctx.EndStageAsync(stageUp, written);
        await ctx.ReportAsync("upsert", 85, $"{written} rows written", completed: written);

        // ── compute_features ───────────────────────────────────
        var stageFeat = await ctx.StartStageAsync("compute_features");
        await ctx.ReportAsync("compute_features", 90, "computing feature columns");
        var feat = await _repo.ComputeAndUpdateFeaturesAsync(table, ctx.CancellationToken);
        await ctx.EndStageAsync(stageFeat, feat);
        await ctx.ReportAsync("done", 100, $"written={written}, features={feat}", completed: written);
    }

    private static Dictionary<long, decimal> ComputeWilderRsi(IList<(long Ts, decimal Close)> closes, int period)
    {
        var result = new Dictionary<long, decimal>();
        if (closes.Count < period + 1) return result;
        decimal gainSum = 0, lossSum = 0;
        for (int i = 1; i <= period; i++)
        {
            var diff = closes[i].Close - closes[i - 1].Close;
            if (diff > 0) gainSum += diff; else lossSum -= diff;
        }
        decimal avgGain = gainSum / period, avgLoss = lossSum / period;
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
        var sorted = src.OrderBy(x => x.Ts).Select(x => (x.Ts, (decimal?)x.Value)).ToList();
        return sorted;
    }

    private static decimal? LookupForwardFill(List<(long Ts, decimal? Value)> src, long ts)
    {
        if (src.Count == 0) return null;
        // binary search for largest Ts <= ts
        int lo = 0, hi = src.Count - 1, best = -1;
        while (lo <= hi)
        {
            int m = (lo + hi) >> 1;
            if (src[m].Ts <= ts) { best = m; lo = m + 1; } else hi = m - 1;
        }
        return best >= 0 ? src[best].Value : null;
    }
}

public sealed class ComputeFeaturesJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.ComputeFeatures;
    private readonly DatasetRepository _repo;
    public ComputeFeaturesJobHandler(DatasetRepository repo) { _repo = repo; }

    public async Task ExecuteAsync(JobContext ctx)
    {
        var p = ctx.Params();
        var table = p.S("table") ?? ctx.Job.TargetTable
            ?? throw new ArgumentException("table required");
        var stage = await ctx.StartStageAsync("compute_features");
        await ctx.ReportAsync("compute_features", 5, $"computing on {table}");
        var n = await _repo.ComputeAndUpdateFeaturesAsync(table, ctx.CancellationToken);
        await ctx.EndStageAsync(stage, n);
        await ctx.ReportAsync("done", 100, $"{n} rows updated", completed: n);
    }
}

public sealed class DetectAnomaliesJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.DetectAnomalies;
    private readonly DatasetRepository _repo;
    public DetectAnomaliesJobHandler(DatasetRepository repo) { _repo = repo; }

    public async Task ExecuteAsync(JobContext ctx)
    {
        var p = ctx.Params();
        var table = p.S("table") ?? ctx.Job.TargetTable
            ?? throw new ArgumentException("table required");
        long? stepMs = p.L("step_ms");

        var stages = new (string Name, Func<Task<long>> Run)[]
        {
            ("gaps",         async () => stepMs is { } sm
                ? (await _repo.DetectGapsAsync(table, sm, ctx.CancellationToken)).Count
                : 0L),
            ("duplicates",   async () => (await _repo.DetectDuplicatesAsync(table, ctx.CancellationToken)).Count),
            ("ohlc",         async () => (await _repo.DetectOhlcViolationsAsync(table, ctx.CancellationToken)).Count),
            ("negatives",    async () => (await _repo.DetectNegativesAsync(table, ctx.CancellationToken)).Count),
            ("zero_streaks", async () => (await _repo.DetectZeroStreaksAsync(table, ct: ctx.CancellationToken)).Count),
            ("stat_outl",    async () => (await _repo.DetectStatisticalOutliersAsync(table, ct: ctx.CancellationToken)).Count),
            ("zscore",       async () => (await _repo.DetectRollingZScoreAsync(table, "close_price", 100, 4.5, "zscore", ctx.CancellationToken)).Count),
            ("stale",        async () => (await _repo.DetectStalePriceAsync(table, "close_price", 5, ctx.CancellationToken)).Count),
            ("ret_outl",     async () => (await _repo.DetectReturnOutliersAsync(table, "close_price", 0.2, ctx.CancellationToken)).Count),
            ("vol_mismatch", async () => (await _repo.DetectVolumeMismatchAsync(table, 0.01, ctx.CancellationToken)).Count),
        };

        long total = 0;
        for (int i = 0; i < stages.Length; i++)
        {
            if (await ctx.IsCancelRequestedAsync()) throw new OperationCanceledException();
            var st = await ctx.StartStageAsync(stages[i].Name);
            try
            {
                var n = await stages[i].Run();
                await ctx.EndStageAsync(st, n);
                total += n;
                var pct = (int)((i + 1) * 100.0 / stages.Length);
                await ctx.ReportAsync(stages[i].Name, pct, $"{stages[i].Name}: {n} rows; total={total}", completed: total);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                await ctx.EndStageAsync(st);
                await ctx.ReportAsync(stages[i].Name, 0, $"{stages[i].Name} failed: {ex.Message}", failed: 1);
            }
        }
        await ctx.ReportAsync("done", 100, $"anomalies={total}", completed: total);
    }
}

public sealed class CleanApplyJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.CleanApply;
    private readonly DatasetRepository _repo;
    private readonly PostgresConnectionFactory _pg;
    public CleanApplyJobHandler(DatasetRepository repo, PostgresConnectionFactory pg) { _repo = repo; _pg = pg; }

    public async Task ExecuteAsync(JobContext ctx)
    {
        var p = ctx.Params();
        var table = p.S("table") ?? ctx.Job.TargetTable
            ?? throw new ArgumentException("table required");
        var ops   = p.S("ops") ?? "";
        var opList = ops.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (opList.Length == 0) opList = new[] { "drop_duplicates", "fix_ohlc", "fill_zero_streaks" };

        long total = 0;
        for (int i = 0; i < opList.Length; i++)
        {
            if (await ctx.IsCancelRequestedAsync()) throw new OperationCanceledException();
            var op = opList[i];
            var st = await ctx.StartStageAsync(op);
            long n;
            await using var conn = await _pg.OpenAsync(ctx.CancellationToken);
            if (op == "drop_duplicates")
                n = await _repo.ApplyDropDuplicatesAsync(table, conn, ct: ctx.CancellationToken);
            else if (op == "fix_ohlc")
                n = await _repo.ApplyFixOhlcAsync(table, conn, ctx.CancellationToken);
            else if (op == "fill_zero_streaks")
                n = await _repo.ApplyFillZeroStreakAsync(table, "close_price", conn, ctx.CancellationToken);
            else n = 0L;
            await ctx.EndStageAsync(st, n);
            total += n;
            var pct = (int)((i + 1) * 100.0 / opList.Length);
            await ctx.ReportAsync(op, pct, $"{op}: {n} rows", completed: total);
        }
        await ctx.ReportAsync("done", 100, $"affected={total}", completed: total);
    }
}

public sealed class ExportJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.Export;
    public Task ExecuteAsync(JobContext ctx) =>
        ctx.ReportAsync("done", 100, "Export-as-job not yet implemented; use legacy cmd.data.dataset.export");
}

public sealed class ImportCsvJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.ImportCsv;
    public Task ExecuteAsync(JobContext ctx) =>
        ctx.ReportAsync("done", 100, "ImportCsv-as-job not yet implemented; use legacy cmd.data.dataset.import_csv");
}

public sealed class UpsertOhlcvJobHandler : IDatasetJobHandler
{
    public string Type => DatasetJobType.UpsertOhlcv;
    public Task ExecuteAsync(JobContext ctx) =>
        ctx.ReportAsync("done", 100, "UpsertOhlcv-as-job: legacy path remains; use cmd.data.dataset.upsert_ohlcv");
}
