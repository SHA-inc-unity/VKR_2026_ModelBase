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
    public async Task<ServiceResult<MarketConverterQuoteResponse>> GetConverterQuoteAsync(
        string fromAsset,
        string toAsset,
        decimal amount,
        string? exchange = null,
        CancellationToken ct = default)
    {
        var normalizedFrom = NormalizeAsset(fromAsset);
        var normalizedTo = NormalizeAsset(toAsset);
        if (string.IsNullOrWhiteSpace(normalizedFrom) || string.IsNullOrWhiteSpace(normalizedTo))
        {
            return ServiceResult<MarketConverterQuoteResponse>.Fail("Both fromAsset and toAsset are required");
        }

        if (amount <= 0)
        {
            return ServiceResult<MarketConverterQuoteResponse>.Fail("Amount must be greater than zero");
        }

        var normalizedExchange = NormalizeExchange(exchange);

        // Per-exchange resolution: if the caller picked an exchange (e.g.
        // Binance, since Bybit is the historical default), prefer the Market
        // Watcher live-quote map filtered to that exchange — that's the
        // single source of truth that the chart, tickers and watcher state
        // share. Fall back to the legacy Bybit linear-tickers snapshot only
        // for the asset side(s) MW does not currently quote on the chosen
        // exchange (USDT-quoted pairs that aren't tracked there).
        var snapshot = await LoadSnapshotAsync(ct);
        var liveRows = !string.IsNullOrWhiteSpace(normalizedExchange)
            ? await LoadRealtimeRowsAsync(normalizedExchange, ct)
            : Array.Empty<RealtimeWatcherRow>();

        var (fromPrice, fromSource) = ResolveConverterUsdPrice(
            normalizedFrom, liveRows, snapshot.Items, normalizedExchange);
        var (toPrice, toSource) = ResolveConverterUsdPrice(
            normalizedTo, liveRows, snapshot.Items, normalizedExchange);

        if (fromPrice is null || toPrice is null || toPrice.Value <= 0)
        {
            return ServiceResult<MarketConverterQuoteResponse>.Fail("Unsupported asset pair for converter quote");
        }

        var rate = fromPrice.Value / toPrice.Value;

        // Source label: if either leg came from MW for the requested exchange,
        // surface that label (e.g. "binance-market-watcher"); otherwise fall
        // back to the snapshot source ("bybit-linear-tickers").
        string sourceLabel;
        if (fromSource == ConverterPriceSource.MarketWatcher
            || toSource == ConverterPriceSource.MarketWatcher)
        {
            var ex = normalizedExchange ?? "market";
            sourceLabel = $"{ex}-market-watcher";
        }
        else
        {
            sourceLabel = SnapshotSource;
        }

        // updatedAt: the freshest of the two legs.
        DateTimeOffset updatedAt = snapshot.UpdatedAt;
        if (!string.IsNullOrWhiteSpace(normalizedExchange))
        {
            var mwUpdatedAt = liveRows
                .Where(r => string.Equals(r.Exchange, normalizedExchange, StringComparison.OrdinalIgnoreCase))
                .Select(r => (DateTimeOffset?)r.UpdatedAt)
                .DefaultIfEmpty(null)
                .Max();
            if (mwUpdatedAt is { } mwUpd && mwUpd > updatedAt)
            {
                updatedAt = mwUpd;
            }
        }

        return ServiceResult<MarketConverterQuoteResponse>.Ok(new MarketConverterQuoteResponse
        {
            FromAsset = normalizedFrom,
            ToAsset = normalizedTo,
            Amount = amount,
            Rate = DecimalRound(rate),
            ConvertedAmount = DecimalRound(amount * rate),
            Source = sourceLabel,
            GeneratedAt = DateTimeOffset.UtcNow,
            UpdatedAt = updatedAt,
        });
    }

    private enum ConverterPriceSource
    {
        None,
        MarketWatcher,
        Snapshot,
    }

    private static (decimal? Price, ConverterPriceSource Source) ResolveConverterUsdPrice(
        string asset,
        IReadOnlyList<RealtimeWatcherRow> liveRows,
        IReadOnlyList<SnapshotTicker> snapshotItems,
        string? exchange)
    {
        if (string.Equals(asset, "USDT", StringComparison.OrdinalIgnoreCase))
        {
            // USDT is the dataset's quote currency; treat as 1 with no
            // attribution to MW or the snapshot — neither was consulted.
            return (1m, ConverterPriceSource.None);
        }

        var symbol = $"{asset}USDT";

        // 1. Prefer Market Watcher's live row for the requested exchange.
        if (!string.IsNullOrWhiteSpace(exchange))
        {
            var match = liveRows
                .Where(r => string.Equals(r.Symbol, symbol, StringComparison.OrdinalIgnoreCase))
                .Where(r => string.Equals(r.Exchange, exchange, StringComparison.OrdinalIgnoreCase))
                .OrderBy(r => r.LagMs)
                .ThenByDescending(r => r.UpdatedAt)
                .FirstOrDefault();
            if (match is not null && match.Price > 0)
            {
                return (match.Price, ConverterPriceSource.MarketWatcher);
            }
        }

        // 2. Fall back to the cached Bybit linear-tickers snapshot. This is
        //    still the right source for asset pairs MW doesn't track on the
        //    chosen exchange — better to surface a known cross-exchange
        //    reference price than to fail the whole quote.
        var snap = snapshotItems
            .FirstOrDefault(item => string.Equals(item.Symbol, symbol, StringComparison.OrdinalIgnoreCase));
        if (snap?.Price > 0)
        {
            return (snap.Price, ConverterPriceSource.Snapshot);
        }

        return (null, ConverterPriceSource.None);
    }
}
