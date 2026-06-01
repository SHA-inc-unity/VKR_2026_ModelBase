using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Clients.Market;

public sealed partial class MarketServiceClient
{
    public async Task<ServiceResult<MarketRealtimeQuotesResponse>> GetRealtimeQuotesAsync(
        IReadOnlyList<string> symbols,
        string? exchange = null,
        CancellationToken ct = default)
    {
        var requestedSymbols = (symbols ?? Array.Empty<string>())
            .Where(static item => !string.IsNullOrWhiteSpace(item))
            .Select(static item => item.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requestedSymbols.Length == 0)
        {
            return ServiceResult<MarketRealtimeQuotesResponse>.Fail("At least one symbol is required");
        }

        var normalizedExchange = NormalizeExchange(exchange);
        var snapshot = await LoadSnapshotAsync(ct);
        var snapshotLookup = snapshot.Items.ToDictionary(item => item.Symbol, StringComparer.OrdinalIgnoreCase);
        var liveRows = await LoadRealtimeRowsAsync(normalizedExchange, ct);

        var liveLookup = liveRows
            .Where(item => requestedSymbols.Contains(item.Symbol, StringComparer.OrdinalIgnoreCase))
            .GroupBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderBy(item => item.LagMs)
                    .ThenBy(item => ExchangePriority(item.Exchange))
                    .ThenByDescending(item => item.UpdatedAt)
                    .First(),
                StringComparer.OrdinalIgnoreCase);

        var items = new List<MarketRealtimeQuoteDto>(requestedSymbols.Length);
        var missing = new List<string>();
        var degradedFields = new HashSet<string>(snapshot.DegradedFields, StringComparer.OrdinalIgnoreCase);

        foreach (var symbol in requestedSymbols)
        {
            snapshotLookup.TryGetValue(symbol, out var snapshotItem);

            if (liveLookup.TryGetValue(symbol, out var liveItem))
            {
                items.Add(ToRealtimeQuoteDto(symbol, snapshotItem, liveItem));
                continue;
            }

            if (snapshotItem is not null)
            {
                degradedFields.Add("realtimePrice");
                items.Add(ToSnapshotFallbackQuoteDto(snapshotItem));
                continue;
            }

            degradedFields.Add("realtimePrice");
            missing.Add(symbol);
        }

        return ServiceResult<MarketRealtimeQuotesResponse>.Ok(new MarketRealtimeQuotesResponse
        {
            Items = items,
            MissingSymbols = missing,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = items.Count > 0 ? items.Max(static item => item.UpdatedAt) : snapshot.UpdatedAt,
                DegradedFields = degradedFields.ToArray(),
            }
        });
    }

    private async Task<IReadOnlyList<RealtimeWatcherRow>> LoadRealtimeRowsAsync(string? exchange, CancellationToken ct)
    {
        var timeout = TimeSpan.FromSeconds(_settings.KafkaTimeoutSeconds);

        try
        {
            var reply = await _kafka.RequestAsync(
                DataTopics.CmdDataMarketWatcherRows,
                new { exchange, limit = RealtimeRowsLimit, offset = 0 },
                timeout,
                ct);

            if (reply.ValueKind != JsonValueKind.Object)
            {
                return [];
            }

            if (reply.TryGetProperty("error", out var errorEl))
            {
                _logger.LogWarning("Realtime watcher rows request returned error: {Error}", errorEl.GetString());
                return [];
            }

            if (!reply.TryGetProperty("items", out var itemsEl) || itemsEl.ValueKind != JsonValueKind.Array)
            {
                return [];
            }

            var rows = new List<RealtimeWatcherRow>();
            foreach (var item in itemsEl.EnumerateArray())
            {
                var parsed = ParseRealtimeWatcherRow(item);
                if (parsed is not null)
                {
                    rows.Add(parsed);
                }
            }

            return rows;
        }
        catch (TimeoutException ex)
        {
            _logger.LogWarning(ex, "Realtime watcher rows request timed out");
            return [];
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Realtime watcher rows request failed");
            return [];
        }
    }

    private static RealtimeWatcherRow? ParseRealtimeWatcherRow(JsonElement item)
    {
        var symbol = GetString(item, "symbol")?.Trim().ToUpperInvariant();
        if (string.IsNullOrWhiteSpace(symbol))
        {
            return null;
        }

        var price = GetDecimal(item, "last_price");
        if (price <= 0)
        {
            return null;
        }

        var updatedAt = GetDateTimeOffset(item, "last_price_ts");
        if (updatedAt is null)
        {
            return null;
        }

        return new RealtimeWatcherRow(
            Symbol: symbol,
            Exchange: NormalizeExchange(GetString(item, "exchange")),
            RealtimeSymbol: GetString(item, "realtime_symbol")?.Trim(),
            Price: DecimalRound(price),
            UpdatedAt: updatedAt.Value,
            LagMs: GetLong(item, "lag_ms"));
    }

    private static MarketRealtimeQuoteDto ToRealtimeQuoteDto(
        string symbol,
        SnapshotTicker? snapshotItem,
        RealtimeWatcherRow liveItem)
    {
        return new MarketRealtimeQuoteDto
        {
            Symbol = symbol,
            Price = liveItem.Price,
            Change24h = snapshotItem?.Change24h ?? 0,
            High24h = snapshotItem?.High24h ?? 0,
            Low24h = snapshotItem?.Low24h ?? 0,
            Volume24h = snapshotItem?.Volume24h ?? 0,
            Exchange = liveItem.Exchange,
            RealtimeSymbol = liveItem.RealtimeSymbol,
            LagMs = liveItem.LagMs,
            Source = RealtimeSource,
            IsFallback = false,
            UpdatedAt = liveItem.UpdatedAt,
        };
    }

    private static MarketRealtimeQuoteDto ToSnapshotFallbackQuoteDto(SnapshotTicker snapshotItem)
    {
        return new MarketRealtimeQuoteDto
        {
            Symbol = snapshotItem.Symbol,
            Price = snapshotItem.Price,
            Change24h = snapshotItem.Change24h,
            High24h = snapshotItem.High24h,
            Low24h = snapshotItem.Low24h,
            Volume24h = snapshotItem.Volume24h,
            Exchange = null,
            RealtimeSymbol = null,
            LagMs = null,
            Source = SnapshotFallbackSource,
            IsFallback = true,
            UpdatedAt = snapshotItem.UpdatedAt,
        };
    }

    private static int ExchangePriority(string? exchange)
    {
        return exchange switch
        {
            "bybit" => 0,
            "binance" => 1,
            _ => 10,
        };
    }

    private sealed record RealtimeWatcherRow(
        string Symbol,
        string? Exchange,
        string? RealtimeSymbol,
        decimal Price,
        DateTimeOffset UpdatedAt,
        long? LagMs);
}
