using System.IO.Compression;
using System.IO.Pipelines;
using System.Text.Json;
using Confluent.Kafka;
using DataService.API.Bybit;
using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Jobs;
using DataService.API.Markets;
using DataService.API.Minio;
using DataService.API.Settings;
using Microsoft.Extensions.Options;
using Npgsql;

namespace DataService.API.Kafka;

public sealed partial class KafkaConsumerService
{
    // ── Ingest pipeline ───────────────────────────────────────────────────

    /// <summary>
    /// Publishes a staged progress event on <see cref="Topics.EvtDataIngestProgress"/>.
    /// Fire-and-forget, errors swallowed inside the producer.
    /// </summary>
    private Task PublishIngestProgressAsync(
        string correlationId, string stage, string label,
        string status, int progress, string? detail, CancellationToken ct)
    {
        if (string.IsNullOrEmpty(correlationId)) return Task.CompletedTask;
        var payload = new
        {
            correlation_id = correlationId,
            stage,
            label,
            status,
            progress,
            detail,
        };
        return _producer.PublishEventAsync(Topics.EvtDataIngestProgress, payload, ct);
    }

    private async Task<object> HandleIngestAsync(
        JsonElement p, string correlationId, CancellationToken ct)
    {
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        var startMs   = TryGetInt64(p, "start_ms");
        var endMs     = TryGetInt64(p, "end_ms");
        var exchange  = (TryGetString(p, "exchange") ?? "bybit").Trim().ToLowerInvariant();
        if (string.IsNullOrEmpty(symbol) || string.IsNullOrEmpty(timeframe)
            || startMs is null || endMs is null)
        {
            return new { error = "missing fields: symbol, timeframe, start_ms, end_ms" };
        }

        string? currentStage = null;
        string? currentLabel = null;

        try
        {
            string key, interval;
            long stepMs;
            (key, interval, stepMs) = DatasetCore.NormalizeTimeframe(timeframe);
            var (s, e) = DatasetCore.NormalizeWindow(startMs.Value, endMs.Value, stepMs);
            var table = DatasetCore.MakeTableName(symbol, key, exchange);
            var market = _markets.GetRequiredClient(exchange);

            // ── Stage: prepare ────────────────────────────────────────────
            currentStage = "prepare"; currentLabel = "Подготовка таблицы";
            await PublishIngestProgressAsync(correlationId, currentStage, currentLabel,
                "running", 0, $"table={table}", ct);

            await _repo.CreateTableIfNotExistsAsync(table, ct);
            var coverage = await _repo.GetCoverageRangeAsync(table, s, e, stepMs, ct);
            if (coverage is { ExpectedInRange: > 0 } && coverage.Value.RowsInRange >= coverage.Value.ExpectedInRange)
            {
                await PublishIngestProgressAsync(correlationId, currentStage, currentLabel,
                    "done", 100, $"missing=0", ct);
                return new { status = "ok", rows_ingested = 0, table };
            }

            var missing = await _repo.FindMissingTimestampsAsync(table, s, e, stepMs, ct);

            await PublishIngestProgressAsync(correlationId, currentStage, currentLabel,
                "done", 100, $"missing={missing.Count}", ct);

            if (missing.Count == 0)
                return new { status = "ok", rows_ingested = 0, table };

            // RSI needs warmup candles (Wilder, period 14) before the requested window.
            const int rsiPeriod = 14;
            var warmupCandles = Math.Max(DatasetConstants.DefaultWarmupCandles, rsiPeriod * 2);
            var fetchStart = s - warmupCandles * stepMs;
            var (oiLabel, oiIntervalMs) = DatasetCore.ChooseOpenInterestInterval(stepMs);

            // Incremental fetch range — only load OI/funding covering the
            // missing slice (plus one step back as a forward-fill buffer).
            // Klines still need full [fetchStart, e] because RSI requires
            // warmup candles before the requested window.
            const long fundingIntervalMs = 28_800_000L; // 8h — Bybit USDT perp default
            var missingStart = missing[0];
            var missingEnd   = missing[^1];
            var fetchOiStart      = missingStart - oiIntervalMs;
            var fetchFundingStart = missingStart - fundingIntervalMs;

            // ── Stage: fetch_klines (with per-page progress callback) ────
            const string klinesStage = "fetch_klines";
            const string klinesLabel = "Загрузка свечей";
            await PublishIngestProgressAsync(correlationId, klinesStage, klinesLabel,
                "running", 0, null, ct);

            var lastPublishedPage = 0;
            var klineTask = market.FetchKlinesAsync(
                symbol.ToUpperInvariant(), interval, fetchStart, e, stepMs, 0, ct,
                onPageDone: (done, total) =>
                {
                    // Throttle: publish at most once every 10 pages (or on the last page).
                    if (done != total && done - lastPublishedPage < 10) return;
                    lastPublishedPage = done;
                    var pct = total > 0 ? (int)Math.Min(99, (long)done * 100 / total) : 0;
                    // fire-and-forget; completion publishes "done" after Task.WhenAll.
                    _ = PublishIngestProgressAsync(correlationId, klinesStage, klinesLabel,
                        "running", pct, $"{done} / {total} страниц", CancellationToken.None);
                });

            // ── Stage: fetch_funding ─────────────────────────────────────
            const string fundingStage = "fetch_funding";
            const string fundingLabel = "Загрузка funding rate";
            await PublishIngestProgressAsync(correlationId, fundingStage, fundingLabel,
                "running", 0, null, ct);
            var fundingTask = market.FetchFundingRatesAsync(
                symbol.ToUpperInvariant(), fetchFundingStart, missingEnd, fundingIntervalMs, ct);

            // ── Stage: fetch_oi ──────────────────────────────────────────
            const string oiStage = "fetch_oi";
            const string oiLabelText = "Загрузка open interest";
            await PublishIngestProgressAsync(correlationId, oiStage, oiLabelText,
                "running", 0, null, ct);
            var oiTask = market.FetchOpenInterestAsync(
                symbol.ToUpperInvariant(), oiLabel, fetchOiStart, missingEnd, oiIntervalMs, ct);

            // Await each independently so we can emit "done" per stage.
            var klines = await klineTask;
            await PublishIngestProgressAsync(correlationId, klinesStage, klinesLabel,
                "done", 100, $"{klines.Count} свечей", ct);

            var funding = await fundingTask;
            await PublishIngestProgressAsync(correlationId, fundingStage, fundingLabel,
                "done", 100, $"{funding.Count} записей", ct);

            var oi = await oiTask;
            await PublishIngestProgressAsync(correlationId, oiStage, oiLabelText,
                "done", 100, $"{oi.Count} записей", ct);

            // Index klines by timestamp for O(1) lookup.
            var klinesByTs = klines.ToDictionary(k => k.TimestampMs, k => k);

            // ── Stage: compute_rsi ───────────────────────────────────────
            const string rsiStage = "compute_rsi";
            const string rsiLabel = "Вычисление RSI";
            await PublishIngestProgressAsync(correlationId, rsiStage, rsiLabel,
                "running", 0, null, ct);

            // ComputeWilderRsiAsync expects (TimestampMs, Close) pairs — extract from full klines.
            var klineCloses = klines.Select(k => (k.TimestampMs, k.Close)).ToList();
            var rsiByTs = await ComputeWilderRsiAsync(
                klineCloses, rsiPeriod,
                onSegmentDone: (done, total) =>
                    PublishIngestProgressAsync(correlationId, rsiStage, rsiLabel,
                        "running", total > 0 ? (int)Math.Min(99, (long)done * 100 / total) : 0,
                        $"{done} / {total} сегментов", CancellationToken.None));

            await PublishIngestProgressAsync(correlationId, rsiStage, rsiLabel,
                "done", 100, $"{rsiByTs.Count} значений", ct);

            // Forward-fill funding + OI to candle timestamps in the requested window.
            var fundingFfill = BuildForwardFill(funding);
            var oiFfill      = BuildForwardFill(oi);

            // Build MarketRow list only for timestamps that are missing.
            var rows = new List<DatasetRepository.MarketRow>(missing.Count);
            foreach (var ts in missing)
            {
                if (!klinesByTs.TryGetValue(ts, out var kline)) continue;
                decimal? fr = LookupForwardFill(fundingFfill, ts);
                decimal? op = LookupForwardFill(oiFfill,      ts);
                decimal? rs = rsiByTs.TryGetValue(ts, out var r) ? r : (decimal?)null;
                rows.Add(new DatasetRepository.MarketRow(
                    TimestampMs:  ts,
                    Symbol:       symbol.ToUpperInvariant(),
                    Exchange:     exchange,
                    Timeframe:    key,
                    OpenPrice:    kline.Open,
                    HighPrice:    kline.High,
                    LowPrice:     kline.Low,
                    ClosePrice:   kline.Close,
                    Volume:       kline.Volume,
                    Turnover:     kline.Turnover,
                    FundingRate:  fr,
                    OpenInterest: op,
                    Rsi:          rs));
            }

            // ── Stage: upsert ────────────────────────────────────────────
            const string upsertStage = "upsert";
            const string upsertLabel = "Запись в базу";
            await PublishIngestProgressAsync(correlationId, upsertStage, upsertLabel,
                "running", 0, $"{rows.Count} строк", ct);

            var written = await _repo.BulkUpsertAsync(table, rows, ct);

            await PublishIngestProgressAsync(correlationId, upsertStage, upsertLabel,
                "done", 100, $"{written} строк записано", ct);

            // ── Stage: compute_features (SQL window-function pass) ──────
            // Не fatal — если upsert прошёл, потеря feature-шага не должна
            // откатывать ingest. Ошибку публикуем как отдельный event и
            // прокидываем в reply через features_error.
            const string featStage = "compute_features";
            const string featLabel = "Вычисление признаков";
            await PublishIngestProgressAsync(correlationId, featStage, featLabel,
                "running", 0, null, ct);

            long featuresUpdated = 0;
            string? featuresError = null;
            var updateFromMs = rows.Count > 0
                ? rows.Min(static row => row.TimestampMs)
                : (long?)null;
            try
            {
                if (updateFromMs is long updateFrom)
                {
                    featuresUpdated = await _repo.ComputeAndUpdateFeaturesSinceAsync(table, updateFrom, ct);
                }
                await PublishIngestProgressAsync(correlationId, featStage, featLabel,
                    "done", 100,
                    updateFromMs is long sinceMs
                        ? $"{featuresUpdated} строк обновлено с {sinceMs}"
                        : "Новые строки не материализованы; пересчёт не потребовался",
                    ct);
            }
            catch (Exception fex)
            {
                featuresError = fex.Message;
                _log.LogError(fex, "compute_features failed for {Table}", table);
                await PublishIngestProgressAsync(correlationId, featStage, featLabel,
                    "error", 0, fex.Message, CancellationToken.None);
            }

            _log.LogInformation(
                "[ingest] {Table} window=[{S},{E}] missing={Missing} fetched_klines={K} funding={F} oi={OI} written={W} features_updated={Feat}",
                table, s, e, missing.Count, klines.Count, funding.Count, oi.Count, written, featuresUpdated);

            return new
            {
                status           = "ok",
                table,
                rows_ingested    = written,
                missing          = missing.Count,
                fetched_klines   = klines.Count,
                fetched_funding  = funding.Count,
                fetched_oi       = oi.Count,
                features_updated = featuresUpdated,
                features_error   = featuresError,
            };
        }
        catch (ArgumentException ex)
        {
            if (currentStage is not null)
                await PublishIngestProgressAsync(correlationId, currentStage, currentLabel ?? currentStage,
                    "error", 0, ex.Message, CancellationToken.None);
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "ingest failed for {Symbol} {Tf}", symbol, timeframe);
            if (currentStage is not null)
                await PublishIngestProgressAsync(correlationId, currentStage, currentLabel ?? currentStage,
                    "error", 0, ex.Message, CancellationToken.None);
            return new { error = ex.Message };
        }
    }

    // ── CSV import (admin-side Upload CSV button) ─────────────────────────
    //
    // Payload: { table: "btcusdt_5m", rows: [{ timestamp_utc: "...", ... }, ...] }
    //
    // Each row object must expose a `timestamp_utc` key (ISO-8601 string OR
    // unix milliseconds) and arbitrary columns matching the market-data schema
    // (symbol, exchange, timeframe, close_price, funding_rate, open_interest,
    // rsi). Missing symbol / exchange / timeframe fall back to values derived
    // from the table name ("btcusdt_5m" → symbol=BTCUSDT, timeframe=5m,
    // exchange=bybit). Rows without a parseable timestamp are skipped and
    // logged — we never abort the whole batch on a malformed row.
    //
    // Reply: { status, rows_imported, rows_skipped, table }
    private async Task<object> HandleImportCsvAsync(JsonElement payload, CancellationToken ct)
    {
        var table = TryGetString(payload, "table");
        if (string.IsNullOrWhiteSpace(table))
            return new { error = "missing field: table" };

        if (payload.ValueKind != JsonValueKind.Object
            || !payload.TryGetProperty("rows", out var rowsEl)
            || rowsEl.ValueKind != JsonValueKind.Array)
        {
            return new { error = "missing field: rows (array)" };
        }

        // Derive defaults from the table name: "{symbol}_{timeframe}".
        var (defaultSymbol, defaultTimeframe) = SplitTableName(table);
        const string defaultExchange = "bybit";

        var rows    = new List<DatasetRepository.MarketRow>(rowsEl.GetArrayLength());
        var skipped = 0;

        foreach (var r in rowsEl.EnumerateArray())
        {
            if (r.ValueKind != JsonValueKind.Object) { skipped++; continue; }

            var tsMs = ParseTimestampMs(r);
            if (tsMs is null)
            {
                skipped++;
                continue;
            }

            var symbol    = ReadCellString(r, "symbol")    ?? defaultSymbol;
            var exchange  = ReadCellString(r, "exchange")  ?? defaultExchange;
            var timeframe = ReadCellString(r, "timeframe") ?? defaultTimeframe;

            var op = ReadCellDecimal(r, "open_price");
            var hp = ReadCellDecimal(r, "high_price");
            var lp = ReadCellDecimal(r, "low_price");
            var cp = ReadCellDecimal(r, "close_price");

            // Phase-4 candle-source-of-truth: a row is only persisted when
            // it carries a complete, internally-consistent OHLC tuple. Rows
            // with partial OHLC data, or with prices that violate the
            // invariant `low ≤ min(open, close) ≤ max(open, close) ≤ high`,
            // are rejected — admitting them would corrupt the
            // "every persisted candle is a single tuple" guarantee.
            if (op is null || hp is null || lp is null || cp is null)
            {
                skipped++;
                continue;
            }
            var loBound = Math.Min(op.Value, cp.Value);
            var hiBound = Math.Max(op.Value, cp.Value);
            if (lp.Value > loBound || hp.Value < hiBound)
            {
                skipped++;
                continue;
            }

            rows.Add(new DatasetRepository.MarketRow(
                TimestampMs:  tsMs.Value,
                Symbol:       (symbol    ?? "").ToUpperInvariant(),
                Exchange:     exchange   ?? defaultExchange,
                Timeframe:    timeframe  ?? defaultTimeframe ?? "",
                OpenPrice:    op,
                HighPrice:    hp,
                LowPrice:     lp,
                ClosePrice:   cp,
                Volume:       ReadCellDecimal(r, "volume"),
                Turnover:     ReadCellDecimal(r, "turnover"),
                FundingRate:  ReadCellDecimal(r, "funding_rate"),
                OpenInterest: ReadCellDecimal(r, "open_interest"),
                Rsi:          ReadCellDecimal(r, "rsi")));
        }

        if (skipped > 0)
        {
            _log.LogWarning(
                "[import_csv] {Table}: skipped {Skipped} row(s) with missing/invalid timestamp_utc",
                table, skipped);
        }

        if (rows.Count == 0)
            return new { status = "ok", table, rows_imported = 0L, rows_skipped = skipped };

        try
        {
            await _repo.CreateTableIfNotExistsAsync(table, ct);
            var written = await _repo.BulkUpsertAsync(table, rows, ct);
            _log.LogInformation(
                "[import_csv] {Table} imported={Written} skipped={Skipped}",
                table, written, skipped);
            return new
            {
                status        = "ok",
                table,
                rows_imported = written,
                rows_skipped  = skipped,
            };
        }
        catch (ArgumentException ex)
        {
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "import_csv failed for {Table}", table);
            return new { error = ex.Message };
        }
    }

    private static (string? Symbol, string? Timeframe) SplitTableName(string table)
    {
        var idx = table.LastIndexOf('_');
        if (idx <= 0 || idx == table.Length - 1) return (null, null);
        return (table[..idx], table[(idx + 1)..]);
    }

    /// <summary>
    /// Parse `timestamp_utc` from a CSV row. Accepts either an ISO-8601 string
    /// or unix milliseconds (as a JSON number OR string of digits). Returns
    /// <c>null</c> for malformed / missing values.
    /// </summary>
    private static long? ParseTimestampMs(JsonElement row)
    {
        if (!row.TryGetProperty("timestamp_utc", out var v)) return null;
        switch (v.ValueKind)
        {
            case JsonValueKind.Number:
                if (v.TryGetInt64(out var asLong)) return asLong;
                if (v.TryGetDouble(out var asDouble)) return (long)asDouble;
                return null;
            case JsonValueKind.String:
                var s = v.GetString();
                if (string.IsNullOrWhiteSpace(s)) return null;
                if (long.TryParse(s, System.Globalization.NumberStyles.Integer,
                                  System.Globalization.CultureInfo.InvariantCulture, out var ms))
                {
                    return ms;
                }
                if (DateTimeOffset.TryParse(s, System.Globalization.CultureInfo.InvariantCulture,
                                            System.Globalization.DateTimeStyles.AssumeUniversal
                                          | System.Globalization.DateTimeStyles.AdjustToUniversal,
                                            out var dto))
                {
                    return dto.ToUnixTimeMilliseconds();
                }
                return null;
            default:
                return null;
        }
    }

    /// <summary>Read a string cell, tolerating JSON numbers/bools and empty strings.</summary>
    private static string? ReadCellString(JsonElement row, string key)
    {
        if (!row.TryGetProperty(key, out var v)) return null;
        return v.ValueKind switch
        {
            JsonValueKind.String => string.IsNullOrEmpty(v.GetString()) ? null : v.GetString(),
            JsonValueKind.Number => v.ToString(),
            JsonValueKind.True   => "true",
            JsonValueKind.False  => "false",
            _                    => null,
        };
    }

    /// <summary>Read a decimal cell. Empty strings / invalid → null (leaves column NULL).</summary>
    private static decimal? ReadCellDecimal(JsonElement row, string key)
    {
        if (!row.TryGetProperty(key, out var v)) return null;
        switch (v.ValueKind)
        {
            case JsonValueKind.Number:
                return v.TryGetDecimal(out var d) ? d : null;
            case JsonValueKind.String:
                var s = v.GetString();
                if (string.IsNullOrWhiteSpace(s)) return null;
                return decimal.TryParse(s, System.Globalization.NumberStyles.Float,
                                        System.Globalization.CultureInfo.InvariantCulture, out var parsed)
                    ? parsed
                    : null;
            default:
                return null;
        }
    }

    /// <summary>
    /// Parallel Wilder's RSI (period N).
    ///
    /// Wilder's smoothing is recursive — each value depends on the previous
    /// <c>avgGain</c>/<c>avgLoss</c>. To parallelise safely we do a single
    /// cheap sequential pass to compute the exact smoothing state at the
    /// start of each segment (seed), then fan out: each worker finishes its
    /// own index range starting from its pre-computed seed, producing the
    /// same values as the sequential algorithm.
    /// </summary>
    private static async Task<Dictionary<long, decimal>> ComputeWilderRsiAsync(
        IReadOnlyList<(long TimestampMs, decimal Close)> klines,
        int period,
        Func<int, int, Task>? onSegmentDone = null)
    {
        var result = new Dictionary<long, decimal>();
        if (klines.Count <= period) return result;

        // 1. Seed the algorithm with the simple average of the first `period`
        //    gains/losses, and emit the first RSI value at index `period`.
        decimal gainSum = 0m, lossSum = 0m;
        for (int i = 1; i <= period; i++)
        {
            var diff = klines[i].Close - klines[i - 1].Close;
            if (diff >= 0) gainSum += diff; else lossSum += -diff;
        }
        decimal avgGain = gainSum / period;
        decimal avgLoss = lossSum / period;
        result[klines[period].TimestampMs] = avgLoss == 0
            ? 100m
            : 100m - 100m / (1m + avgGain / avgLoss);

        // Index range [first, last] that still needs computing: (period, klines.Count - 1].
        int first = period + 1;
        int last  = klines.Count - 1;
        int work  = last - first + 1;
        if (work <= 0)
        {
            if (onSegmentDone is not null) await onSegmentDone(1, 1);
            return result;
        }

        // 2. Choose a segment count based on CPU and work size.
        int segCount = Math.Clamp(Environment.ProcessorCount, 2, 8);
        segCount = Math.Min(segCount, work);
        if (segCount <= 1)
        {
            // Degrade to sequential for very small ranges.
            for (int i = first; i <= last; i++)
            {
                var diff = klines[i].Close - klines[i - 1].Close;
                var gain = diff > 0 ?  diff : 0m;
                var loss = diff < 0 ? -diff : 0m;
                avgGain = (avgGain * (period - 1) + gain) / period;
                avgLoss = (avgLoss * (period - 1) + loss) / period;
                result[klines[i].TimestampMs] = avgLoss == 0
                    ? 100m
                    : 100m - 100m / (1m + avgGain / avgLoss);
            }
            if (onSegmentDone is not null) await onSegmentDone(1, 1);
            return result;
        }

        // Compute segment boundaries: equal-ish slices of [first, last].
        var bounds = new (int From, int To)[segCount];
        {
            int chunk = work / segCount;
            int rem   = work % segCount;
            int cursor = first;
            for (int si = 0; si < segCount; si++)
            {
                int size = chunk + (si < rem ? 1 : 0);
                bounds[si] = (cursor, cursor + size - 1);
                cursor += size;
            }
        }

        // 3. Sequential "warm" pass — compute the exact (avgGain, avgLoss)
        //    state at the start of each segment. Arithmetic only, O(n).
        var seeds = new (decimal AvgGain, decimal AvgLoss)[segCount];
        seeds[0] = (avgGain, avgLoss);
        {
            decimal g = avgGain, l = avgLoss;
            int nextSeg = 1;
            for (int i = first; i <= last && nextSeg < segCount; i++)
            {
                var diff = klines[i].Close - klines[i - 1].Close;
                var gain = diff > 0 ?  diff : 0m;
                var loss = diff < 0 ? -diff : 0m;
                g = (g * (period - 1) + gain) / period;
                l = (l * (period - 1) + loss) / period;
                if (i + 1 == bounds[nextSeg].From)
                {
                    seeds[nextSeg] = (g, l);
                    nextSeg++;
                }
            }
        }

        // 4. Fan out: each worker computes its own slice and returns the
        //    partial dictionary. No shared state writes.
        int completed = 0;
        var partials = new Dictionary<long, decimal>[segCount];
        var workers = new Task[segCount];
        for (int si = 0; si < segCount; si++)
        {
            int idx = si;
            var (from, to) = bounds[idx];
            var (seedGain, seedLoss) = seeds[idx];
            workers[idx] = Task.Run(() =>
            {
                var local = new Dictionary<long, decimal>(to - from + 1);
                decimal g = seedGain, l = seedLoss;
                for (int i = from; i <= to; i++)
                {
                    var diff = klines[i].Close - klines[i - 1].Close;
                    var gain = diff > 0 ?  diff : 0m;
                    var loss = diff < 0 ? -diff : 0m;
                    g = (g * (period - 1) + gain) / period;
                    l = (l * (period - 1) + loss) / period;
                    local[klines[i].TimestampMs] = l == 0
                        ? 100m
                        : 100m - 100m / (1m + g / l);
                }
                partials[idx] = local;

                if (onSegmentDone is not null)
                {
                    var done = Interlocked.Increment(ref completed);
                    try { _ = onSegmentDone(done, segCount); } catch { /* ignore */ }
                }
            });
        }
        await Task.WhenAll(workers);

        // 5. Merge partials.
        foreach (var part in partials)
            foreach (var kv in part) result[kv.Key] = kv.Value;

        return result;
    }

    /// <summary>
    /// Sorted array of (timestampMs, value) used by <see cref="LookupForwardFill"/>.
    /// Input is assumed sorted ascending.
    /// </summary>
    private static (long[] Ts, decimal[] Vals) BuildForwardFill(
        IReadOnlyList<(long TimestampMs, decimal Value)> series)
    {
        var ts = new long[series.Count];
        var vs = new decimal[series.Count];
        for (int i = 0; i < series.Count; i++)
        {
            ts[i] = series[i].TimestampMs;
            vs[i] = series[i].Value;
        }
        return (ts, vs);
    }

    /// <summary>
    /// Forward-fill: return the value with the greatest timestamp &lt;= target.
    /// Returns null when target precedes the first observation.
    /// </summary>
    private static decimal? LookupForwardFill(
        (long[] Ts, decimal[] Vals) ffill, long targetMs)
    {
        var idx = Array.BinarySearch(ffill.Ts, targetMs);
        if (idx >= 0) return ffill.Vals[idx];
        idx = ~idx - 1;                       // largest < target
        if (idx < 0) return null;
        return ffill.Vals[idx];
    }
}
