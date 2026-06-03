using NotificationService.Application.DTOs;
using NotificationService.Application.Interfaces;
using NotificationService.Domain.Entities;

namespace NotificationService.Application.Services;

public interface IPriceAlertsAppService
{
    Task<IReadOnlyList<AlertResponse>> ListAsync(Guid userId, CancellationToken ct);
    Task<AlertResponse> CreateAsync(Guid userId, CreateAlertRequest req, CancellationToken ct);

    /// <summary>Update an alert owned by the user; returns null if not found / not owned.</summary>
    Task<AlertResponse?> UpdateAsync(Guid userId, Guid id, UpdateAlertRequest req, CancellationToken ct);

    /// <summary>Delete an alert owned by the user; returns false if not found / not owned.</summary>
    Task<bool> DeleteAsync(Guid userId, Guid id, CancellationToken ct);
}

public sealed class PriceAlertsAppService : IPriceAlertsAppService
{
    private readonly IPriceAlertRepository _repo;

    public PriceAlertsAppService(IPriceAlertRepository repo) => _repo = repo;

    public async Task<IReadOnlyList<AlertResponse>> ListAsync(Guid userId, CancellationToken ct)
    {
        var alerts = await _repo.ListByUserAsync(userId, ct);
        return alerts.Select(Map).ToList();
    }

    public async Task<AlertResponse> CreateAsync(Guid userId, CreateAlertRequest req, CancellationToken ct)
    {
        // Domain Create validates the condition (above/below) and uppercases the
        // symbol; an invalid condition throws ArgumentException → 400 via middleware.
        var alert = PriceAlert.Create(userId, req.Symbol, req.Condition, req.TargetPrice, req.IsEnabled);
        await _repo.AddAsync(alert, ct);
        return Map(alert);
    }

    public async Task<AlertResponse?> UpdateAsync(Guid userId, Guid id, UpdateAlertRequest req, CancellationToken ct)
    {
        var alert = await _repo.GetAsync(id, userId, ct);
        if (alert is null) return null;

        alert.Update(req.Symbol, req.Condition, req.TargetPrice, req.IsEnabled);
        await _repo.UpdateAsync(alert, ct);
        return Map(alert);
    }

    public Task<bool> DeleteAsync(Guid userId, Guid id, CancellationToken ct) =>
        _repo.DeleteAsync(id, userId, ct);

    private static AlertResponse Map(PriceAlert a) => new()
    {
        Id = a.Id.ToString("N"),
        Symbol = a.Symbol,
        Condition = a.Condition,
        TargetPrice = a.TargetPrice,
        IsEnabled = a.IsEnabled,
        CreatedAt = new DateTimeOffset(DateTime.SpecifyKind(a.CreatedAt, DateTimeKind.Utc)),
    };
}
