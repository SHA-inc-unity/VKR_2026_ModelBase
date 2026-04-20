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
        _logger.LogDebug("Notifications service is not yet available; returning stub failure");
        return Task.FromResult(ServiceResult<IReadOnlyList<NotificationDto>>.Fail("Notifications service not yet implemented"));
    }
}
