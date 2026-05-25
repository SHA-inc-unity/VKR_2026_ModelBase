using System.Net;
using System.Net.Http.Json;

namespace GatewayService.API.Clients.Account;

public interface IAccountInternalClient
{
    /// <summary>
    /// Fetches the user's active decrypted API key/secret for the given
    /// exchange from microservice_account via its internal endpoint
    /// (header <c>X-Internal-Api-Key</c>). Returns null when no key is set.
    /// </summary>
    Task<DecryptedExchangeKey?> GetActiveKeyAsync(Guid userId, string exchange, CancellationToken ct = default);
}

public sealed record DecryptedExchangeKey
{
    public Guid Id { get; init; }
    public Guid UserId { get; init; }
    public string Exchange { get; init; } = string.Empty;
    public string ApiKey { get; init; } = string.Empty;
    public string ApiSecret { get; init; } = string.Empty;
    public bool CanRead { get; init; }
    public bool CanTrade { get; init; }
}

public sealed class AccountInternalClient : IAccountInternalClient
{
    private readonly HttpClient _http;
    private readonly ILogger<AccountInternalClient> _logger;

    public AccountInternalClient(HttpClient http, ILogger<AccountInternalClient> logger)
    {
        _http = http;
        _logger = logger;
    }

    public async Task<DecryptedExchangeKey?> GetActiveKeyAsync(Guid userId, string exchange, CancellationToken ct = default)
    {
        try
        {
            using var response = await _http.GetAsync($"internal/api-keys/{userId}?exchange={Uri.EscapeDataString(exchange)}", ct);
            if (response.StatusCode == HttpStatusCode.NotFound) return null;
            if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                _logger.LogWarning("Account internal endpoint refused our X-Internal-Api-Key header.");
                return null;
            }
            response.EnsureSuccessStatusCode();
            return await response.Content.ReadFromJsonAsync<DecryptedExchangeKey>(cancellationToken: ct);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogWarning(ex, "Account internal API-key fetch failed");
            return null;
        }
    }
}
