using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Clients.Notifications;

public interface INotificationsServiceClient
{
    Task<ServiceResult<IReadOnlyList<NotificationDto>>> GetForUserAsync(string userId, int limit = 50, CancellationToken ct = default);
}
