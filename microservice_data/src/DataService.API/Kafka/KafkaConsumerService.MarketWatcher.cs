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
    private object HandleMarketWatcherStatus() => new
    {
        watcher = BuildMarketWatcherSnapshot(_marketWatcher.GetSnapshot()),
    };

    private object HandleMarketWatcherSetEnabled(JsonElement payload)
    {
        var enabled = TryGetBool(payload, "enabled");
        if (enabled is null)
        {
            return new { error = "enabled is required", code = "bad_request" };
        }

        _marketWatcher.SetDesiredEnabled(enabled.Value, "api");
        return new
        {
            ok = true,
            desired_enabled = enabled.Value,
            watcher = BuildMarketWatcherSnapshot(_marketWatcher.GetSnapshot()),
        };
    }

    private static object BuildMarketWatcherSnapshot(MarketWatcherStatusSnapshot snapshot) => new
    {
        desiredEnabled = snapshot.DesiredEnabled,
        effectiveEnabled = snapshot.EffectiveEnabled,
        status = snapshot.Status,
        message = snapshot.Message,
        startedAtMs = snapshot.StartedAtMs,
        lastHeartbeatAtMs = snapshot.LastHeartbeatAtMs,
        lastFlushAtMs = snapshot.LastFlushAtMs,
        lastTickAtMs = snapshot.LastTickAtMs,
        configuredPairs = snapshot.ConfiguredPairs,
        trackedSymbols = snapshot.TrackedSymbols,
        liveRows = snapshot.LiveRows,
        perExchange = snapshot.PerExchange.Select(x => new { exchange = x.Exchange, symbols = x.Symbols }),
        avgLagMs = snapshot.AverageLagMs,
        maxLagMs = snapshot.MaxLagMs,
        ticksInLastWindow = snapshot.TicksInLastWindow,
        lastFlushRows = snapshot.LastFlushRows,
        exchanges = snapshot.Exchanges,
        timeframes = snapshot.Timeframes,
        lastError = snapshot.LastError,
        lastErrorAtMs = snapshot.LastErrorAtMs,
    };

    private Task<object> HandleMarketWatcherRowsAsync(JsonElement payload, CancellationToken ct)
    {
        var exchange = TryGetString(payload, "exchange");
        var search = TryGetString(payload, "search");
        var limit = (int?)TryGetInt64(payload, "limit") ?? 100;
        var offset = (int?)TryGetInt64(payload, "offset") ?? 0;
        var page = _marketWatcher.ReadLiveRows(exchange, search, limit, offset);
        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        return Task.FromResult<object>(new
        {
            items = page.Items.Select(item => new
            {
                exchange = item.Exchange,
                symbol = item.Symbol,
                realtime_symbol = item.RealtimeSymbol,
                last_price = item.LastPrice,
                last_price_ts = DateTimeOffset.FromUnixTimeMilliseconds(item.LastPriceTimestampMs).ToUniversalTime().ToString("O"),
                updated_at = DateTimeOffset.FromUnixTimeMilliseconds(item.UpdatedAtMs).ToUniversalTime().ToString("O"),
                lag_ms = Math.Max(0, now - item.LastPriceTimestampMs),
                candles_json = item.Frames.ToDictionary(frame => frame, _ => (object?)null),
            }),
            total = page.Total,
            limit = page.Limit,
            offset = page.Offset,
        });
    }

    private object HandleMarketWatcherLogs(JsonElement payload)
    {
        var limit = (int?)TryGetInt64(payload, "limit") ?? 200;
        var logs = _marketWatcher.ReadLogs(limit).Select(entry => new
        {
            id = entry.Id,
            ts = entry.Ts,
            level = entry.Level,
            evt = entry.Event,
            message = entry.Message,
            fields = entry.Fields,
        });

        return new { logs };
    }

    /// <summary>
    /// Returns dataset symbols MW is actively tracking on the given exchange.
    /// Backs the gateway's <c>GET /api/v1/market/config?exchange=…</c> so the
    /// user-facing dropdown lists only symbols whose `{exchange}_{symbol}_…`
    /// tables are being filled in real time. Falls back to an empty list when
    /// MW has not yet completed its first discovery cycle — callers are
    /// expected to layer their own persisted fallback (see MarketConfigService).
    /// </summary>
    private async Task<object> HandleMarketWatcherTrackedSymbolsAsync(JsonElement payload, CancellationToken ct)
    {
        var exchange = TryGetString(payload, "exchange");
        if (string.IsNullOrWhiteSpace(exchange))
        {
            return new { error = "exchange is required", code = "bad_request" };
        }

        var normalizedExchange = exchange.Trim().ToLowerInvariant();
        var liveSymbols = _marketWatcher.GetTrackedSymbols(normalizedExchange);
        if (liveSymbols.Count > 0)
        {
            return new { exchange = normalizedExchange, symbols = liveSymbols };
        }

        // MW state can be empty for a few seconds after restart, or longer if
        // an exchange's subscription is currently retrying. Fall back to the
        // set of persisted `{exchange}_{symbol}_*` tables — every row in there
        // is something MW has previously written.
        var dbSymbols = await ListPersistedSymbolsAsync(normalizedExchange, ct);
        return new { exchange = normalizedExchange, symbols = dbSymbols };
    }

    private async Task<IReadOnlyList<string>> ListPersistedSymbolsAsync(string exchange, CancellationToken ct)
    {
        try
        {
            var tables = await _repo.ListTablesAsync(ct);
            // For bybit the table name is `{symbol}_{timeframe}`; for every other
            // exchange it is `{exchange}_{symbol}_{timeframe}` (DatasetCore.MakeTableName).
            var prefix = string.Equals(exchange, "bybit", StringComparison.OrdinalIgnoreCase)
                ? string.Empty
                : $"{exchange}_";

            var result = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var table in tables)
            {
                var name = table?.Trim();
                if (string.IsNullOrEmpty(name)) continue;

                if (prefix.Length > 0)
                {
                    if (!name.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) continue;
                    name = name[prefix.Length..];
                }
                else
                {
                    // For bybit, skip rows that look like another exchange's tables
                    // (e.g. binance_btcusdt_5m would otherwise match). Also skip
                    // legacy kraken_* tables that may still exist from before the
                    // Kraken integration was removed.
                    if (name.StartsWith("binance_", StringComparison.OrdinalIgnoreCase)
                        || name.StartsWith("kraken_", StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                }

                // Strip trailing `_<timeframe>` (e.g. `_5m`, `_60m`, `_1d`).
                var underscore = name.LastIndexOf('_');
                if (underscore <= 0 || underscore >= name.Length - 1) continue;
                var symbol = name[..underscore].ToUpperInvariant();
                if (!symbol.EndsWith("USDT", StringComparison.OrdinalIgnoreCase)) continue;
                result.Add(symbol);
            }

            return result.OrderBy(s => s, StringComparer.OrdinalIgnoreCase).ToArray();
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex,
                "Failed to list persisted symbols for {Exchange}; returning empty",
                exchange);
            return Array.Empty<string>();
        }
    }
}
