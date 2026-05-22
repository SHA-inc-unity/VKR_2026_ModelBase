using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;

namespace GatewayService.API.Clients.Account;

public interface IAccountServiceClient
{
    Task<ServiceResult<AccountUserDto>> GetCurrentUserAsync(string bearerToken, CancellationToken ct = default);
}
