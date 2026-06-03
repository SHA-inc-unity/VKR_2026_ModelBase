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
public async Task<ServiceResult<IReadOnlyList<TrendingAssetDto>>> GetTrendingAsync(int limit = 10, CancellationToken ct = default)
    {
        var snapshot = await LoadSnapshotAsync(ct);
        var items = snapshot.Items
            .OrderByDescending(item => item.TrendingScore)
            .ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase)
            .Take(Math.Max(1, limit))
            .Select(item => new TrendingAssetDto
            {
                Symbol = item.Symbol,
                PriceUsd = item.Price,
                ChangePercent24h = item.Change24h,
            })
            .ToArray();

        return ServiceResult<IReadOnlyList<TrendingAssetDto>>.Ok(items);
    }

public async Task<ServiceResult<MarketTickersResponse>> GetTickersAsync(
        int page = 1,
        int pageSize = 25,
        string? search = null,
        string? sortBy = null,
        string? sortDir = null,
        IReadOnlyList<string>? symbols = null,
        string? collection = null,
        CancellationToken ct = default)
    {
        var snapshot = await LoadSnapshotAsync(ct);
        var normalizedCollection = NormalizeCollection(collection);

        page = Math.Max(1, page);
        pageSize = Math.Clamp(pageSize, 1, 100);

        IEnumerable<SnapshotTicker> filtered = snapshot.Items;

        if (symbols is { Count: > 0 })
        {
            var filter = symbols
                .Where(static item => !string.IsNullOrWhiteSpace(item))
                .Select(static item => item.Trim().ToUpperInvariant())
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
            filtered = filtered.Where(item => filter.Contains(item.Symbol));
        }

        if (!string.IsNullOrWhiteSpace(search))
        {
            var term = search.Trim();
            filtered = filtered.Where(item =>
                item.Symbol.Contains(term, StringComparison.OrdinalIgnoreCase)
                || item.DisplayName.Contains(term, StringComparison.OrdinalIgnoreCase)
                || item.BaseAsset.Contains(term, StringComparison.OrdinalIgnoreCase)
                || item.QuoteAsset.Contains(term, StringComparison.OrdinalIgnoreCase));
        }

        filtered = ApplyCollection(filtered, normalizedCollection);

        sortBy = ResolveSortBy(sortBy, normalizedCollection);
        sortDir = ResolveSortDir(sortDir, sortBy);

        var ordered = OrderTickers(filtered, sortBy, sortDir).ToArray();
        var total = ordered.Length;
        var pageItems = ordered
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .Select(ToTickerItemDto)
            .ToArray();

        return ServiceResult<MarketTickersResponse>.Ok(new MarketTickersResponse
        {
            SnapshotId = BuildSnapshotId(snapshot.UpdatedAt),
            Collection = normalizedCollection,
            Items = pageItems,
            Total = total,
            Page = page,
            PageSize = pageSize,
            Search = string.IsNullOrWhiteSpace(search) ? null : search.Trim(),
            SortBy = sortBy,
            SortDir = sortDir,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = snapshot.UpdatedAt,
                DegradedFields = snapshot.DegradedFields,
            }
        });
    }

    public async Task<ServiceResult<MarketBatchQuotesResponse>> GetQuotesAsync(IReadOnlyList<string> symbols, CancellationToken ct = default)
    {
        var requestedSymbols = (symbols ?? Array.Empty<string>())
            .Where(static item => !string.IsNullOrWhiteSpace(item))
            .Select(static item => item.Trim().ToUpperInvariant())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

        if (requestedSymbols.Length == 0)
        {
            return ServiceResult<MarketBatchQuotesResponse>.Fail("At least one symbol is required");
        }

        var snapshot = await LoadSnapshotAsync(ct);
        var lookup = snapshot.Items.ToDictionary(item => item.Symbol, StringComparer.OrdinalIgnoreCase);

        var items = new List<MarketQuoteDto>(requestedSymbols.Length);
        var missing = new List<string>();
        foreach (var symbol in requestedSymbols)
        {
            if (!lookup.TryGetValue(symbol, out var item))
            {
                missing.Add(symbol);
                continue;
            }

            items.Add(new MarketQuoteDto
            {
                Symbol = item.Symbol,
                Price = DecimalRound(item.Price),
                Change24h = DecimalRound(item.Change24h),
                High24h = DecimalRound(item.High24h),
                Low24h = DecimalRound(item.Low24h),
                Volume24h = DecimalRound(item.Volume24h),
                UpdatedAt = snapshot.UpdatedAt,
            });
        }

        return ServiceResult<MarketBatchQuotesResponse>.Ok(new MarketBatchQuotesResponse
        {
            SnapshotId = BuildSnapshotId(snapshot.UpdatedAt),
            Items = items,
            MissingSymbols = missing,
            Meta = new FrontendResponseMetaDto
            {
                GeneratedAt = DateTimeOffset.UtcNow,
                UpdatedAt = snapshot.UpdatedAt,
                DegradedFields = snapshot.DegradedFields,
            }
        });
    }

    private static IEnumerable<SnapshotTicker> ApplyCollection(IEnumerable<SnapshotTicker> source, string collection)
    {
        return collection switch
        {
            "trending" => source.Where(item => item.IsTrending || item.TrendingScore > 0),
            "top-movers" => source.Where(item => item.Change24h != 0),
            _ => source,
        };
    }

    private static string NormalizeCollection(string? collection)
    {
        return collection?.Trim().ToLowerInvariant() switch
        {
            "trending" => "trending",
            "top-movers" => "top-movers",
            _ => "market"
        };
    }

    private static string ResolveSortBy(string? sortBy, string collection)
    {
        var value = sortBy?.Trim().ToLowerInvariant();
        return value switch
        {
            "symbol" or "displayname" or "price" or "change24h" or "volume24h" or "marketcap" or "high24h" or "low24h" or "rank" or "updatedat" => value,
            "trending" => "trending",
            "top-movers" or "topmovers" => "top-movers",
            _ => collection switch
            {
                "trending" => "trending",
                "top-movers" => "top-movers",
                _ => "rank"
            }
        };
    }

    private static string ResolveSortDir(string? sortDir, string sortBy)
    {
        if (!string.IsNullOrWhiteSpace(sortDir))
        {
            return string.Equals(sortDir, "asc", StringComparison.OrdinalIgnoreCase) ? "asc" : "desc";
        }

        return string.Equals(sortBy, "rank", StringComparison.OrdinalIgnoreCase) ? "asc" : "desc";
    }

    private static IOrderedEnumerable<SnapshotTicker> OrderTickers(IEnumerable<SnapshotTicker> source, string sortBy, string sortDir)
    {
        var descending = !string.Equals(sortDir, "asc", StringComparison.OrdinalIgnoreCase);
        return (sortBy, descending) switch
        {
            ("symbol", false) => source.OrderBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("symbol", true) => source.OrderByDescending(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("displayname", false) => source.OrderBy(item => item.DisplayName, StringComparer.OrdinalIgnoreCase),
            ("displayname", true) => source.OrderByDescending(item => item.DisplayName, StringComparer.OrdinalIgnoreCase),
            ("price", false) => source.OrderBy(item => item.Price),
            ("price", true) => source.OrderByDescending(item => item.Price),
            ("change24h", false) => source.OrderBy(item => item.Change24h),
            ("change24h", true) => source.OrderByDescending(item => item.Change24h),
            ("volume24h", false) => source.OrderBy(item => item.Volume24h),
            ("volume24h", true) => source.OrderByDescending(item => item.Volume24h),
            // Sort by the REAL market cap; coins with unknown supply (null cap)
            // sort last in both directions so the displayed leaderboard is honest.
            ("marketcap", false) => source.OrderByDescending(item => item.MarketCap.HasValue).ThenBy(item => item.MarketCap ?? 0m),
            ("marketcap", true) => source.OrderByDescending(item => item.MarketCap.HasValue).ThenByDescending(item => item.MarketCap ?? 0m),
            ("trending", false) => source.OrderBy(item => item.TrendingScore).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("trending", true) => source.OrderByDescending(item => item.TrendingScore).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("top-movers", false) => source.OrderBy(item => Math.Abs(item.Change24h)).ThenBy(item => item.Change24h).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("top-movers", true) => source.OrderByDescending(item => Math.Abs(item.Change24h)).ThenByDescending(item => item.Change24h).ThenBy(item => item.Symbol, StringComparer.OrdinalIgnoreCase),
            ("high24h", false) => source.OrderBy(item => item.High24h),
            ("high24h", true) => source.OrderByDescending(item => item.High24h),
            ("low24h", false) => source.OrderBy(item => item.Low24h),
            ("low24h", true) => source.OrderByDescending(item => item.Low24h),
            ("updatedat", false) => source.OrderBy(item => item.UpdatedAt),
            ("updatedat", true) => source.OrderByDescending(item => item.UpdatedAt),
            ("rank", false) => source.OrderBy(item => item.Rank),
            _ => source.OrderByDescending(item => item.Rank),
        };
    }

    private static string BuildSnapshotId(DateTimeOffset updatedAt)
    {
        return updatedAt.ToUnixTimeMilliseconds().ToString(CultureInfo.InvariantCulture);
    }

    private static MarketTickerItemDto ToTickerItemDto(SnapshotTicker item)
    {
        return new MarketTickerItemDto
        {
            Symbol = item.Symbol,
            DisplayName = item.DisplayName,
            BaseAsset = item.BaseAsset,
            QuoteAsset = item.QuoteAsset,
            Price = item.Price,
            Change24h = item.Change24h,
            Volume24h = item.Volume24h,
            MarketCap = item.MarketCap,
            CirculatingSupply = item.CirculatingSupply,
            TotalSupply = item.TotalSupply,
            MaxSupply = item.MaxSupply,
            Fdv = item.Fdv,
            Ath = item.Ath,
            High24h = item.High24h,
            Low24h = item.Low24h,
            Rank = item.Rank,
            LogoUrl = item.LogoUrl,
            ExchangeCount = item.ExchangeCount,
            UpdatedAt = item.UpdatedAt,
            IsTrending = item.IsTrending,
        };
    }
}
