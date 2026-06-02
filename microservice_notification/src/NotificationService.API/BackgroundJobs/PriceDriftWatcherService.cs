using System.Collections.Concurrent;
using System.Text.Json;
using Microsoft.Extensions.Options;
using NotificationService.Application.Common.Settings;
using NotificationService.Application.Interfaces;
using NotificationService.Application.Services;
using NotificationService.Domain.Entities;

namespace NotificationService.API.BackgroundJobs;

/// <summary>
/// Periodically snapshots tracked symbols and emits price.favorite notifications
/// when a user's favorite drifts beyond their threshold compared to the last
/// snapshot we have on hand.
/// </summary>
public sealed class PriceDriftWatcherService : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly PriceWatcherSettings _settings;
    private readonly ILogger<PriceDriftWatcherService> _log;
    private readonly ConcurrentDictionary<string, decimal> _lastPrices = new(StringComparer.OrdinalIgnoreCase);

    public PriceDriftWatcherService(
        IServiceScopeFactory scopes,
        IOptions<PriceWatcherSettings> opts,
        ILogger<PriceDriftWatcherService> log)
    {
        _scopes = scopes;
        _settings = opts.Value;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_settings.Enabled)
        {
            _log.LogInformation("Price-drift watcher disabled");
            return;
        }

        try { await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken); }
        catch (OperationCanceledException) { return; }

        var period = TimeSpan.FromSeconds(Math.Max(60, _settings.PollIntervalSeconds));
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await RunOnceAsync(stoppingToken); }
            catch (OperationCanceledException) { break; }
            catch (Exception ex) { _log.LogWarning(ex, "Price-drift tick failed"); }

            try { await Task.Delay(period, stoppingToken); }
            catch (OperationCanceledException) { break; }
        }
    }

    private async Task RunOnceAsync(CancellationToken ct)
    {
        using var scope = _scopes.CreateScope();
        var social = scope.ServiceProvider.GetRequiredService<ISocialDirectoryService>();
        var market = scope.ServiceProvider.GetRequiredService<IMarketSnapshotService>();
        var notif = scope.ServiceProvider.GetRequiredService<INotificationsAppService>();
        var settingsRepo = scope.ServiceProvider.GetRequiredService<INotificationSettingsRepository>();

        // Track exactly the symbols people actually favorited (from social),
        // unioned with a small well-known baseline. This replaces the old
        // hard-coded-only list, so a favorite outside the common 20 still gets
        // price alerts. We then ask social who favorites each and ping the
        // market — keeping the watcher O(symbols) rather than O(users).
        var favorited = await social.GetAllFavoritedSymbolsAsync(ct);
        var trackedSymbols = WellKnownSymbols
            .Concat(favorited.Select(s => s.Trim().ToUpperInvariant()))
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.Ordinal)
            .ToArray();

        var prices = await market.GetSnapshotAsync(trackedSymbols, ct);
        if (prices.Count == 0) return;

        foreach (var (symbol, price) in prices)
        {
            var users = await social.GetFavoriteUsersBySymbolAsync(symbol, ct);
            if (users.Count == 0)
            {
                _lastPrices[symbol] = price;
                continue;
            }

            if (!_lastPrices.TryGetValue(symbol, out var prev) || prev <= 0)
            {
                _lastPrices[symbol] = price;
                continue;
            }

            var deltaPct = (price - prev) / prev * 100m;
            var absDelta = Math.Abs(deltaPct);

            foreach (var userId in users)
            {
                var s = await settingsRepo.GetOrCreateAsync(userId, ct);
                if (!s.EnablePrice) continue;
                if (absDelta < s.PriceThresholdPct) continue;

                // 24h dedup bucket.
                var bucket = DateTime.UtcNow.Date.Ticks / TimeSpan.TicksPerDay;
                var dedup = $"{symbol}:{bucket}:{Math.Sign(deltaPct)}";
                var direction = deltaPct >= 0 ? "+" : "";
                var n = Notification.Create(
                    userId: userId,
                    kind: "price.favorite",
                    title: $"{symbol} moved {direction}{deltaPct:F2}%",
                    body: $"Now {price}, was {prev}",
                    deeplink: $"/asset/{symbol}",
                    payloadJson: JsonSerializer.Serialize(new { symbol, prev, price, deltaPct }),
                    dedupKey: dedup);

                await notif.PushAsync(n, ct);
            }

            _lastPrices[symbol] = price;
        }
    }

    private static readonly string[] WellKnownSymbols =
    [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
        "TRXUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "TONUSDT", "LTCUSDT", "MATICUSDT",
        "ATOMUSDT", "NEARUSDT", "ETCUSDT", "FILUSDT", "ARBUSDT", "OPUSDT",
    ];
}
