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
    private Task PublishAnaliticRepairProgressAsync(
        string correlationId, string stage, string label,
        string status, int progress, string? detail, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(correlationId)) return Task.CompletedTask;
        var payload = new
        {
            correlation_id = correlationId,
            stage,
            label,
            status,
            progress,
            detail,
        };
        return _producer.PublishEventAsync(EvtAnaliticDatasetRepairProgress, payload, ct);
    }

    // ── OHLCV upsert (Analitic-orchestrated repair) ───────────────────────

    /// <summary>
    /// Handle <c>cmd.data.dataset.repair_ohlcv</c>: exchange-aware OHLCV repair
    /// that reuses the data-service market clients and upserts only raw OHLCV
    /// columns, preserving all non-OHLCV fields.
    /// </summary>
    private async Task<object> HandleRepairOhlcvAsync(
        JsonElement p, string correlationId, CancellationToken ct)
    {
        var symbol    = TryGetString(p, "symbol");
        var timeframe = TryGetString(p, "timeframe");
        var startMs   = TryGetInt64(p, "start_ms");
        var endMs     = TryGetInt64(p, "end_ms");
        var exchange  = (TryGetString(p, "exchange") ?? "bybit").Trim().ToLowerInvariant();
        var progressCorrelationId = TryGetString(p, "progress_correlation_id") ?? correlationId;
        if (string.IsNullOrWhiteSpace(symbol) || string.IsNullOrWhiteSpace(timeframe)
            || startMs is null || endMs is null)
        {
            return new { error = "missing fields: symbol, timeframe, start_ms, end_ms" };
        }

        var startedAt = DateTimeOffset.UtcNow;
        string? currentStage = null;
        string? currentLabel = null;

        try
        {
            var (key, interval, stepMs) = DatasetCore.NormalizeTimeframe(timeframe);
            var (s, e) = DatasetCore.NormalizeWindow(startMs.Value, endMs.Value, stepMs);
            var table = DatasetCore.MakeTableName(symbol, key, exchange);
            var market = _markets.GetRequiredClient(exchange);

            currentStage = "prepare";
            currentLabel = "Подготовка";
            await PublishAnaliticRepairProgressAsync(
                progressCorrelationId, currentStage, currentLabel,
                "running", 0, null, ct);

            await _repo.CreateTableIfNotExistsAsync(table, ct);

            await PublishAnaliticRepairProgressAsync(
                progressCorrelationId, currentStage, currentLabel,
                "done", 100, table, ct);

            currentStage = "fetch";
            currentLabel = "Загрузка свечей";
            await PublishAnaliticRepairProgressAsync(
                progressCorrelationId, currentStage, currentLabel,
                "running", 0, null, ct);

            var lastPublishedPage = 0;
            var klines = await market.FetchKlinesAsync(
                symbol.ToUpperInvariant(), interval, s, e, stepMs, 0, ct,
                onPageDone: (done, total) =>
                {
                    if (done != total && done - lastPublishedPage < 10) return;
                    lastPublishedPage = done;
                    var pct = total > 0 ? (int)Math.Min(99, (long)done * 100 / total) : 0;
                    _ = PublishAnaliticRepairProgressAsync(
                        progressCorrelationId, currentStage, currentLabel,
                        "running", pct, $"{done} / {total} страниц", CancellationToken.None);
                });

            await PublishAnaliticRepairProgressAsync(
                progressCorrelationId, currentStage, currentLabel,
                "done", 100, $"{klines.Count:N0} свечей", ct);

            currentStage = "upsert";
            currentLabel = "Запись в базу";
            await PublishAnaliticRepairProgressAsync(
                progressCorrelationId, currentStage, currentLabel,
                "running", 0, $"{klines.Count:N0} строк", ct);

            if (klines.Count == 0)
            {
                await PublishAnaliticRepairProgressAsync(
                    progressCorrelationId, currentStage, currentLabel,
                    "done", 100, "нет данных", ct);
                return new
                {
                    table,
                    rows_fetched = 0,
                    rows_affected = 0,
                    elapsed_sec = Math.Round((DateTimeOffset.UtcNow - startedAt).TotalSeconds, 2),
                };
            }

            long totalAffected = 0;
            long sentRows = 0;
            for (var offset = 0; offset < klines.Count; offset += DatasetConstants.UpsertBatchSize)
            {
                var size = Math.Min(DatasetConstants.UpsertBatchSize, klines.Count - offset);
                var batch = new List<DatasetRepository.OhlcvRow>(size);
                for (var i = offset; i < offset + size; i++)
                {
                    var row = klines[i];
                    batch.Add(new DatasetRepository.OhlcvRow(
                        row.TimestampMs,
                        row.Open,
                        row.High,
                        row.Low,
                        row.Close,
                        row.Volume,
                        row.Turnover));
                }

                totalAffected += await _repo.BulkUpdateOhlcvAsync(
                    table, symbol, exchange, key, batch, ct);
                sentRows += batch.Count;

                await PublishAnaliticRepairProgressAsync(
                    progressCorrelationId, currentStage, currentLabel,
                    "running",
                    Math.Min(99, (int)(sentRows * 100 / klines.Count)),
                    $"{sentRows:N0}/{klines.Count:N0} строк",
                    ct);
            }

            await PublishAnaliticRepairProgressAsync(
                progressCorrelationId, currentStage, currentLabel,
                "done", 100, $"{totalAffected:N0} обновлено", ct);

            return new
            {
                table,
                rows_fetched = klines.Count,
                rows_affected = totalAffected,
                elapsed_sec = Math.Round((DateTimeOffset.UtcNow - startedAt).TotalSeconds, 2),
            };
        }
        catch (ArgumentException ex)
        {
            if (currentStage is not null && currentLabel is not null)
            {
                await PublishAnaliticRepairProgressAsync(
                    progressCorrelationId, currentStage, currentLabel,
                    "error", 0, ex.Message, ct);
            }
            return new { error = ex.Message };
        }
        catch (InvalidOperationException ex)
        {
            if (currentStage is not null && currentLabel is not null)
            {
                await PublishAnaliticRepairProgressAsync(
                    progressCorrelationId, currentStage, currentLabel,
                    "error", 0, ex.Message, ct);
            }
            return new { error = ex.Message };
        }
        catch (Exception ex)
        {
            _log.LogError(ex,
                "repair_ohlcv failed for {Exchange}:{Symbol}:{Timeframe}",
                exchange, symbol, timeframe);
            if (currentStage is not null && currentLabel is not null)
            {
                await PublishAnaliticRepairProgressAsync(
                    progressCorrelationId, currentStage, currentLabel,
                    "error", 0, ex.Message, ct);
            }
            return new { error = ex.Message };
        }
    }

    /// <summary>
    /// Handle <c>cmd.data.dataset.upsert_ohlcv</c>: merges OHLCV rows into an
    /// existing market table, preserving any non-OHLCV columns on conflict.
    /// </summary>
    /// <remarks>
    /// Payload shape:
    /// <code>
    /// {
    ///   "table":     string (required),
    ///   "symbol":    string (required, used for fresh-row identity),
    ///   "exchange":  string (default "bybit"),
    ///   "timeframe": string (required),
    ///   "rows": [
    ///     { "ts_ms": long, "open": number, "high": number,
    ///       "low": number, "close": number,
    ///       "volume": number?, "turnover": number? },
    ///     ...
    ///   ]
    /// }
    /// </code>
    /// Phase-4 candle-source-of-truth contract: every row must carry a full
    /// O/H/L/C tuple sourced from the same kline. Rows that omit any of the
    /// four prices, or whose prices violate the OHLC invariant
    /// (<c>low ≤ min(open, close) ≤ max(open, close) ≤ high</c>), are
    /// rejected — a single hybrid row would otherwise corrupt the
    /// "every persisted candle is a single tuple" guarantee.
    ///
    /// Reply: <c>{ rows_affected: long, rows_rejected: long,
    /// rejection_reasons: string[] }</c> on success, <c>{ error }</c>
    /// otherwise.
    /// </remarks>
    private async Task<object> HandleUpsertOhlcvAsync(JsonElement p, CancellationToken ct)
    {
        var table     = TryGetString(p, "table");
        var symbol    = TryGetString(p, "symbol");
        var exchange  = TryGetString(p, "exchange") ?? "bybit";
        var timeframe = TryGetString(p, "timeframe");
        if (string.IsNullOrWhiteSpace(table)
            || string.IsNullOrWhiteSpace(symbol)
            || string.IsNullOrWhiteSpace(timeframe))
        {
            return new { error = "missing fields: table, symbol, timeframe" };
        }
        if (!p.TryGetProperty("rows", out var rowsEl) || rowsEl.ValueKind != JsonValueKind.Array)
        {
            return new { error = "missing field: rows (array)" };
        }

        var parsed   = new List<DatasetRepository.OhlcvRow>(rowsEl.GetArrayLength());
        var rejected = 0L;
        var reasons  = new List<string>();
        foreach (var item in rowsEl.EnumerateArray())
        {
            var ts = TryGetInt64(item, "ts_ms");
            if (ts is null) { rejected++; continue; }

            var o = TryGetDecimal(item, "open");
            var h = TryGetDecimal(item, "high");
            var l = TryGetDecimal(item, "low");
            var c = TryGetDecimal(item, "close");
            if (o is null || h is null || l is null || c is null)
            {
                rejected++;
                if (reasons.Count < 5)
                    reasons.Add($"ts={ts.Value}: missing OHLC field (open/high/low/close all required under candle-source-of-truth)");
                continue;
            }

            // OHLC invariant check — if violated, the four prices came from
            // different sources (or one is corrupt) and cannot form a valid
            // candle.
            var lo = Math.Min(o.Value, c.Value);
            var hi = Math.Max(o.Value, c.Value);
            if (l.Value > lo || h.Value < hi)
            {
                rejected++;
                if (reasons.Count < 5)
                    reasons.Add($"ts={ts.Value}: OHLC violation (low={l} open={o} close={c} high={h})");
                continue;
            }

            parsed.Add(new DatasetRepository.OhlcvRow(
                ts.Value, o, h, l, c,
                TryGetDecimal(item, "volume"),
                TryGetDecimal(item, "turnover")));
        }
        if (parsed.Count == 0)
            return new { rows_affected = 0L, rows_rejected = rejected, rejection_reasons = reasons };

        try
        {
            await _repo.CreateTableIfNotExistsAsync(table, ct);
            var affected = await _repo.BulkUpdateOhlcvAsync(
                table, symbol, exchange, timeframe, parsed, ct);
            return new
            {
                rows_affected     = affected,
                rows_rejected     = rejected,
                rejection_reasons = reasons,
            };
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "upsert_ohlcv failed for {Table}", table);
            return new { error = ex.Message };
        }
    }
}
