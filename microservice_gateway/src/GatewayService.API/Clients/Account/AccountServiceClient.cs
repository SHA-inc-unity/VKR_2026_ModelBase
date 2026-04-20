using System.Net.Http.Headers;
using System.Net.Http.Json;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;

namespace GatewayService.API.Clients.Account;

public sealed class AccountServiceClient : IAccountServiceClient
{
    private readonly HttpClient _http;
    private readonly ILogger<AccountServiceClient> _logger;

    public AccountServiceClient(HttpClient http, ILogger<AccountServiceClient> logger)
    {
        _http = http;
        _logger = logger;
    }

    public async Task<ServiceResult<AccountUserDto>> GetCurrentUserAsync(string bearerToken, CancellationToken ct = default)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, "api/account/me");
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);

            using var response = await _http.SendAsync(request, ct);

            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning("Account service returned {StatusCode} for /me", response.StatusCode);
                return ServiceResult<AccountUserDto>.Fail($"Account service returned {(int)response.StatusCode}");
            }

            var user = await response.Content.ReadFromJsonAsync<AccountUserDto>(
                options: new System.Text.Json.JsonSerializerOptions { PropertyNameCaseInsensitive = true },
                cancellationToken: ct);

            return user is not null
                ? ServiceResult<AccountUserDto>.Ok(user)
                : ServiceResult<AccountUserDto>.Fail("Account service returned empty body");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
        {
            _logger.LogWarning(ex, "Account service call failed");
            return ServiceResult<AccountUserDto>.Fail(ex.Message);
        }
    }
}
