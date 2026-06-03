using System.Globalization;
using System.Text.Json;
using GatewayService.API.Clients.Market;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <summary>
/// Lazy, cache-backed implementation of <see cref="ICoinMetadataService"/>.
///
/// <para>
/// Fetches CoinGecko <c>/coins/markets?vs_currency=usd&amp;ids=&lt;curated&gt;</c>
/// once per cache TTL (default 6h — supply moves slowly) and projects each row
/// into a <see cref="CoinMetadata"/> keyed by the gateway base asset. Mirrors the
/// canonical-overview caching pattern in <c>MarketServiceClient.Overview.cs</c>
/// (lazy <see cref="IMarketCacheService.GetOrCreateAsync"/>, no hosted service)
/// and the soft-fail contract of <c>TryFetchCoinGeckoGlobalAsync</c>: any failure
/// logs a warning and yields an empty map, so the snapshot degrades to null caps
/// rather than breaking.
/// </para>
///
/// <para>
/// Reuses the <c>nameof(MarketServiceClient)</c> HttpClient (already carries the
/// CoinGecko-friendly <c>User-Agent</c>/<c>Accept</c> headers) and optionally adds
/// the demo-tier <c>x-cg-demo-api-key</c> header when configured.
/// </para>
/// </summary>
public sealed class CoinMetadataService : ICoinMetadataService
{
    private const string CacheKey = "market:coin-metadata:v1";

    // CoinGecko id -> gateway base asset, inverted from the curated map so we can
    // re-key the /coins/markets rows (which are keyed by coin id) back to bases.
    private static readonly IReadOnlyDictionary<string, string> IdToBase =
        BuildIdToBaseMap();

    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IMarketCacheService _cache;
    private readonly MarketSettings _settings;
    private readonly ILogger<CoinMetadataService> _logger;

    public CoinMetadataService(
        IHttpClientFactory httpClientFactory,
        IMarketCacheService cache,
        IOptions<MarketSettings> settings,
        ILogger<CoinMetadataService> logger)
    {
        _httpClientFactory = httpClientFactory;
        _cache = cache;
        _settings = settings.Value;
        _logger = logger;
    }

    /// <inheritdoc />
    public async Task<IReadOnlyDictionary<string, CoinMetadata>> GetMetadataAsync(CancellationToken ct = default)
    {
        var ttl = TimeSpan.FromSeconds(Math.Max(60, _settings.CoinMetadataCacheTtlSeconds));
        var envelope = await _cache.GetOrCreateAsync(
            CacheKey,
            ttl,
            () => FetchMetadataAsync(ct),
            ct);

        return envelope.Items;
    }

    private async Task<CoinMetadataEnvelope> FetchMetadataAsync(CancellationToken ct)
    {
        var ids = CoinGeckoIdMap.AllCoinGeckoIds;
        if (ids.Count == 0)
        {
            return new CoinMetadataEnvelope(new Dictionary<string, CoinMetadata>(StringComparer.OrdinalIgnoreCase));
        }

        var idList = string.Join(',', ids);
        // per_page=250 covers the curated universe (~86 ids) in a single page;
        // price_change_percentage=24h keeps the row shape consistent with the
        // wider markets payload (we ignore the field but request it for parity).
        var url = $"{_settings.CoinGeckoBaseUrl.TrimEnd('/')}/coins/markets" +
                  $"?vs_currency=usd&ids={Uri.EscapeDataString(idList)}" +
                  "&per_page=250&page=1&price_change_percentage=24h";

        try
        {
            using var http = _httpClientFactory.CreateClient(nameof(MarketServiceClient));
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            if (!string.IsNullOrWhiteSpace(_settings.CoinGeckoApiKey))
            {
                request.Headers.TryAddWithoutValidation("x-cg-demo-api-key", _settings.CoinGeckoApiKey);
            }

            using var response = await http.SendAsync(request, ct);
            response.EnsureSuccessStatusCode();

            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

            if (doc.RootElement.ValueKind != JsonValueKind.Array)
            {
                throw new InvalidOperationException("CoinGecko /coins/markets returned a non-array payload");
            }

            var map = new Dictionary<string, CoinMetadata>(StringComparer.OrdinalIgnoreCase);
            foreach (var row in doc.RootElement.EnumerateArray())
            {
                var id = GetString(row, "id")?.Trim();
                if (string.IsNullOrWhiteSpace(id) || !IdToBase.TryGetValue(id, out var baseAsset))
                {
                    continue;
                }

                map[baseAsset] = new CoinMetadata(
                    CirculatingSupply: GetPositiveDecimal(row, "circulating_supply"),
                    TotalSupply: GetPositiveDecimal(row, "total_supply"),
                    MaxSupply: GetPositiveDecimal(row, "max_supply"),
                    Ath: GetPositiveDecimal(row, "ath"));
            }

            _logger.LogInformation(
                "Coin metadata refreshed: {Resolved}/{Curated} curated ids carried supply data",
                map.Count, ids.Count);

            return new CoinMetadataEnvelope(map);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Failed to fetch per-coin metadata from CoinGecko; serving empty metadata map");
            return new CoinMetadataEnvelope(new Dictionary<string, CoinMetadata>(StringComparer.OrdinalIgnoreCase));
        }
    }

    private static Dictionary<string, string> BuildIdToBaseMap()
    {
        var map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var (baseAsset, id) in CoinGeckoIdMap.Entries)
        {
            // The curated map is collision-safe on bases; if two bases ever shared
            // an id, first-write-wins keeps the build deterministic.
            map.TryAdd(id, baseAsset);
        }
        return map;
    }

    private static string? GetString(JsonElement item, string name)
    {
        return item.TryGetProperty(name, out var property) && property.ValueKind == JsonValueKind.String
            ? property.GetString()
            : null;
    }

    private static decimal? GetPositiveDecimal(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        decimal value = property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetDecimal(out var numberValue) => numberValue,
            JsonValueKind.String when decimal.TryParse(
                property.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0m,
        };

        return value > 0m ? value : null;
    }

    /// <summary>
    /// JSON-serializable cache envelope (the distributed cache round-trips via JSON;
    /// wrapping the dictionary in a class keeps deserialization unambiguous).
    /// </summary>
    public sealed record CoinMetadataEnvelope(Dictionary<string, CoinMetadata> Items);
}
