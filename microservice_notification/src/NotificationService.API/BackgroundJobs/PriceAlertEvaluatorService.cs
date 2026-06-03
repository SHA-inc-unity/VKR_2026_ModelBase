using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;
using NotificationService.Application.Common.Settings;
using NotificationService.Application.Interfaces;
using NotificationService.Application.Services;
using NotificationService.Domain.Entities;

namespace NotificationService.API.BackgroundJobs;

/// <summary>
/// Evaluates user-defined price alerts against live prices and fires a
/// <c>price.alert</c> notification when a symbol crosses the alert's target in the
/// chosen direction. Mirrors <see cref="PriceDriftWatcherService"/>'s cadence /
/// kill-switch / warm-up structure and reuses the same <see cref="IMarketSnapshotService"/>.
///
/// Fire-once: an alert disarms when it fires (so a sustained breach does not spam),
/// and re-arms automatically once the price crosses back to the non-triggering side.
/// </summary>
public sealed class PriceAlertEvaluatorService : BackgroundService
{
    private readonly IServiceScopeFactory _scopes;
    private readonly AlertWatcherSettings _settings;
    private readonly ILogger<PriceAlertEvaluatorService> _log;

    public PriceAlertEvaluatorService(
        IServiceScopeFactory scopes,
        IOptions<AlertWatcherSettings> opts,
        ILogger<PriceAlertEvaluatorService> log)
    {
        _scopes = scopes;
        _settings = opts.Value;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_settings.Enabled)
        {
            _log.LogInformation("Price-alert evaluator disabled");
            return;
        }

        try { await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken); }
        catch (OperationCanceledException) { return; }

        var period = TimeSpan.FromSeconds(Math.Max(30, _settings.PollIntervalSeconds));
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await RunOnceAsync(stoppingToken); }
            catch (OperationCanceledException) { break; }
            catch (Exception ex) { _log.LogWarning(ex, "Price-alert tick failed"); }

            try { await Task.Delay(period, stoppingToken); }
            catch (OperationCanceledException) { break; }
        }
    }

    private async Task RunOnceAsync(CancellationToken ct)
    {
        using var scope = _scopes.CreateScope();
        var repo = scope.ServiceProvider.GetRequiredService<IPriceAlertRepository>();
        var market = scope.ServiceProvider.GetRequiredService<IMarketSnapshotService>();
        var notif = scope.ServiceProvider.GetRequiredService<INotificationsAppService>();

        var alerts = await repo.ListEnabledAsync(ct);
        if (alerts.Count == 0) return;

        var symbols = alerts.Select(a => a.Symbol).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        var prices = await market.GetSnapshotAsync(symbols, ct);
        if (prices.Count == 0) return;

        foreach (var alert in alerts)
        {
            if (!prices.TryGetValue(alert.Symbol, out var price)) continue;

            var met = alert.Condition == "above" ? price >= alert.TargetPrice : price <= alert.TargetPrice;

            if (met && alert.IsArmed)
            {
                await FireAsync(notif, alert, price, ct);
                alert.MarkFired(price);
                await repo.UpdateAsync(alert, ct);
            }
            else if (!met && !alert.IsArmed)
            {
                // Price crossed back to the non-triggering side — let it fire again later.
                alert.ReArm();
                await repo.UpdateAsync(alert, ct);
            }
        }
    }

    private static async Task FireAsync(INotificationsAppService notif, PriceAlert alert, decimal price, CancellationToken ct)
    {
        var directionWord = alert.Condition == "above" ? "выше" : "ниже";
        var targetText = alert.TargetPrice.ToString(CultureInfo.InvariantCulture);
        var priceText = price.ToString(CultureInfo.InvariantCulture);

        // Dedup per fire: a new epoch each time it re-arms+fires lets the same alert
        // notify again, while a still-armed re-evaluation in the same tick is a no-op.
        var firedEpoch = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        var n = Notification.Create(
            userId: alert.UserId,
            kind: "price.alert",
            title: $"{alert.Symbol}: цена {directionWord} {targetText}",
            body: $"Сейчас {priceText}",
            deeplink: $"/asset/{alert.Symbol}",
            payloadJson: JsonSerializer.Serialize(new
            {
                alertId = alert.Id.ToString("N"),
                symbol = alert.Symbol,
                condition = alert.Condition,
                targetPrice = alert.TargetPrice,
                price,
            }),
            dedupKey: $"alert:{alert.Id:N}:{firedEpoch}");

        await notif.PushAsync(n, ct);
    }
}
