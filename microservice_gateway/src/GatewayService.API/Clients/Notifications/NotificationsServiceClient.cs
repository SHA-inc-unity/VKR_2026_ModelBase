using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Notifications;

/// <summary>Stub — Notification Service is not yet implemented.</summary>
public sealed class NotificationsServiceClient : INotificationsServiceClient
{
    private readonly ILogger<NotificationsServiceClient> _logger;

    public NotificationsServiceClient(ILogger<NotificationsServiceClient> logger) => _logger = logger;

    public Task<ServiceResult<IReadOnlyList<NotificationDto>>> GetForUserAsync(string userId, int limit = 50, CancellationToken ct = default)
    {
        _logger.LogDebug("Notifications service fallback is active; returning an empty gateway-local inbox for {UserId}", userId);
        return Task.FromResult(ServiceResult<IReadOnlyList<NotificationDto>>.Ok(Array.Empty<NotificationDto>()));
    }
}
