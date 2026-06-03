using GatewayService.API.DTOs.Requests;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Settings;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.Options;
using System.Text.Json;

namespace GatewayService.API.Frontend;

public sealed class FrontendContractState : IFrontendContractState
{
    private const string StorageKey = "gateway:frontend-contract-state:v1";
    private static readonly DistributedCacheEntryOptions StorageOptions = new()
    {
        AbsoluteExpirationRelativeToNow = TimeSpan.FromDays(180)
    };

    private static readonly AvailableExchangeDto[] ExchangeCatalog =
    [
        new() { Id = "binance", Name = "Binance", Slug = "binance", IsActive = true },
        new() { Id = "bybit", Name = "Bybit", Slug = "bybit", IsActive = true },
    ];

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly object _gate = new();
    private readonly IDistributedCache _cache;
    private readonly ILogger<FrontendContractState> _log;
    private readonly Dictionary<string, Dictionary<string, LinkedExchangeDto>> _linkedByUser =
        new(StringComparer.OrdinalIgnoreCase);

    private ServiceTogglesDto _serviceToggles;

    public FrontendContractState(
        IDistributedCache cache,
        IOptions<FeatureFlagsSettings> featureFlags,
        ILogger<FrontendContractState> log)
    {
        _cache = cache;
        _log = log;

        var flags = featureFlags.Value;
        _serviceToggles = new ServiceTogglesDto
        {
            News = flags.News,
            Alerts = flags.Notifications,
            PortfolioSync = flags.Portfolio,
            MarketOverview = flags.Market,
        };
    }

    public PortfolioSummaryDto GetDashboardPortfolioSummary(string userId)
    {
        var summary = GetPortfolioSummary(userId);
        return new PortfolioSummaryDto
        {
            TotalValueUsd = summary.TotalValue,
            PnlPercent24h = summary.TotalPnlPercent,
            AssetCount = summary.AssetCount,
        };
    }

    public PortfolioDetailedSummaryResponse GetPortfolioSummary(string userId)
    {
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            var linked = GetLinkedUnsafe(userId);
            var byExchange = linked
                .Select(item => new PortfolioExchangeSummaryDto
                {
                    Exchange = item.Name,
                    TotalValue = item.CachedBalance,
                    Change24h = 0,
                    IsSynced = false,
                    LastSyncedAt = item.LinkedAt,
                    Holdings = [],
                })
                .ToArray();

            return new PortfolioDetailedSummaryResponse
            {
                TotalValue = byExchange.Sum(item => item.TotalValue),
                TotalPnl = 0,
                TotalPnlPercent = 0,
                AssetCount = 0,
                ExchangeCount = byExchange.Length,
                ByAsset = [],
                ByExchange = byExchange,
            };
        }
    }

    public IReadOnlyList<AvailableExchangeDto> GetAvailableExchanges(string userId)
    {
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            var connected = GetLinkedUnsafe(userId)
                .Select(item => item.Slug)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);

            return ExchangeCatalog
                .Select(item => item with { IsConnected = connected.Contains(item.Slug) })
                .ToArray();
        }
    }

    public IReadOnlyList<LinkedExchangeDto> GetLinkedExchanges(string userId)
    {
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            return GetLinkedUnsafe(userId).ToArray();
        }
    }

    public LinkedExchangeDto? LinkExchange(string userId, LinkExchangeRequest request)
    {
        var slug = NormalizeSlug(request.Slug);
        var catalogEntry = ExchangeCatalog.FirstOrDefault(item => string.Equals(item.Slug, slug, StringComparison.OrdinalIgnoreCase));
        if (catalogEntry is null)
        {
            return null;
        }

        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            var userLinks = GetOrCreate(_linkedByUser, userId);
            var linked = new LinkedExchangeDto
            {
                Name = catalogEntry.Name,
                Slug = catalogEntry.Slug,
                MaskedKey = MaskKey(request.ApiKey),
                CachedBalance = 0,
                IsActive = true,
                LinkedAt = DateTimeOffset.UtcNow,
            };

            userLinks[slug] = linked;
            PersistUnsafe();
            return linked;
        }
    }

    public LinkedExchangeDto? UpdateExchange(string userId, string slug, UpdateExchangeLinkRequest request)
    {
        slug = NormalizeSlug(slug);
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            if (!_linkedByUser.TryGetValue(userId, out var userLinks)
                || !userLinks.TryGetValue(slug, out var linked))
            {
                return null;
            }

            linked = linked with
            {
                MaskedKey = string.IsNullOrWhiteSpace(request.ApiKey) ? linked.MaskedKey : MaskKey(request.ApiKey),
                IsActive = request.IsActive ?? linked.IsActive,
            };

            userLinks[slug] = linked;
            PersistUnsafe();
            return linked;
        }
    }

    public bool DeleteExchange(string userId, string slug)
    {
        slug = NormalizeSlug(slug);
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            var removed = _linkedByUser.TryGetValue(userId, out var userLinks)
                && userLinks.Remove(slug);
            if (removed)
            {
                PersistUnsafe();
            }

            return removed;
        }
    }

    public ServiceTogglesDto GetServiceToggles()
    {
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            return _serviceToggles;
        }
    }

    public ServiceTogglesDto UpdateServiceToggles(PatchServiceTogglesRequest request)
    {
        lock (_gate)
        {
            HydrateFromCacheUnsafe();
            _serviceToggles = _serviceToggles with
            {
                News = request.News ?? _serviceToggles.News,
                Alerts = request.Alerts ?? _serviceToggles.Alerts,
                PortfolioSync = request.PortfolioSync ?? _serviceToggles.PortfolioSync,
                MarketOverview = request.MarketOverview ?? _serviceToggles.MarketOverview,
            };

            PersistUnsafe();
            return _serviceToggles;
        }
    }

    public FrontendAdminSnapshot GetAdminSnapshot()
    {
        lock (_gate)
        {
            HydrateFromCacheUnsafe();

            return new FrontendAdminSnapshot(
                UsersCount: _linkedByUser.Keys.Count,
                LinkedExchangesCount: _linkedByUser.Values.Sum(item => item.Count),
                // TODO: re-source alert count from notification service
                // Alerts now live in microservice_notification, not the gateway.
                AlertsCount: 0,
                AvailableExchangesCount: ExchangeCatalog.Length,
                ServiceToggles: _serviceToggles);
        }
    }

    private static Dictionary<string, TValue> GetOrCreate<TValue>(
        Dictionary<string, Dictionary<string, TValue>> source,
        string userId)
    {
        if (!source.TryGetValue(userId, out var value))
        {
            value = new Dictionary<string, TValue>(StringComparer.OrdinalIgnoreCase);
            source[userId] = value;
        }

        return value;
    }

    private List<LinkedExchangeDto> GetLinkedUnsafe(string userId)
    {
        if (!_linkedByUser.TryGetValue(userId, out var userLinks))
        {
            return [];
        }

        return userLinks.Values
            .OrderBy(item => item.Name, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private static string NormalizeSlug(string slug) => slug.Trim().ToLowerInvariant();

    private static string MaskKey(string rawKey)
    {
        var value = rawKey.Trim();
        if (value.Length <= 4)
        {
            return "****";
        }

        if (value.Length <= 8)
        {
            return $"{value[..2]}****{value[^2..]}";
        }

        return $"{value[..4]}****{value[^4..]}";
    }

    private void HydrateFromCacheUnsafe()
    {
        try
        {
            var payload = _cache.GetString(StorageKey);
            if (string.IsNullOrWhiteSpace(payload))
            {
                return;
            }

            var persisted = JsonSerializer.Deserialize<PersistedFrontendContractState>(payload, JsonOptions);
            if (persisted is null)
            {
                return;
            }

            _linkedByUser.Clear();
            foreach (var (userId, links) in persisted.LinkedByUser)
            {
                _linkedByUser[userId] = links
                    .ToDictionary(item => item.Slug, StringComparer.OrdinalIgnoreCase);
            }

            _serviceToggles = persisted.ServiceToggles;
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to hydrate frontend contract state from distributed cache");
        }
    }

    private void PersistUnsafe()
    {
        try
        {
            var persisted = new PersistedFrontendContractState
            {
                LinkedByUser = _linkedByUser.ToDictionary(
                    pair => pair.Key,
                    pair => (IReadOnlyList<LinkedExchangeDto>)pair.Value.Values.ToArray(),
                    StringComparer.OrdinalIgnoreCase),
                ServiceToggles = _serviceToggles,
            };

            _cache.SetString(StorageKey, JsonSerializer.Serialize(persisted, JsonOptions), StorageOptions);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to persist frontend contract state to distributed cache");
        }
    }

    private sealed record PersistedFrontendContractState
    {
        public Dictionary<string, IReadOnlyList<LinkedExchangeDto>> LinkedByUser { get; init; } =
            new(StringComparer.OrdinalIgnoreCase);

        public ServiceTogglesDto ServiceToggles { get; init; } = new();
    }
}