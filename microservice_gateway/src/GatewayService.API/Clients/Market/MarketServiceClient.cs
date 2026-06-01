using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Clients.Market;

public sealed partial class MarketServiceClient : IMarketServiceClient
{
    private const string SnapshotCacheKey = "market:snapshot:linear:v1";
    private const string CanonicalOverviewCacheKey = "market:overview:canonical:v2";
    private const string GlobalSummaryCacheKeyPrefix = "market:summary:tracked:v2";
    private const string SnapshotSource = "bybit-linear-tickers";
    private const string RealtimeSource = "market-watch-live";
    private const string SnapshotFallbackSource = "snapshot-fallback";
    private const int RealtimeRowsLimit = 500;

    private readonly IMarketConfigService _marketConfig;
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly IMarketCacheService _cache;
    private readonly IKafkaRequestClient _kafka;
    private readonly MarketSettings _settings;
    private readonly ILogger<MarketServiceClient> _logger;

    public MarketServiceClient(
        IMarketConfigService marketConfig,
        IHttpClientFactory httpClientFactory,
        IMarketCacheService cache,
        IKafkaRequestClient kafka,
        IOptions<MarketSettings> settings,
        ILogger<MarketServiceClient> logger)
    {
        _marketConfig = marketConfig;
        _httpClientFactory = httpClientFactory;
        _cache = cache;
        _kafka = kafka;
        _settings = settings.Value;
        _logger = logger;
    }

    private static DateTimeOffset? ResolveCompositeUpdatedAt(params DateTimeOffset?[] values)
    {
        var available = values.Where(value => value.HasValue).Select(value => value!.Value).ToArray();
        if (available.Length == 0)
        {
            return null;
        }

        return available.Min();
    }

    private static decimal DecimalRound(decimal value) => decimal.Round(value, 6, MidpointRounding.AwayFromZero);
}
