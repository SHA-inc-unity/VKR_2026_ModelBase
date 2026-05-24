using System.Net.Http.Json;
using SocialService.Application.Interfaces.Services;

namespace SocialService.API.Services;

public sealed class AccountServiceSettings
{
    public const string SectionName = "AccountService";

    public string BaseUrl { get; set; } = "http://account-api:5000";
    public string InternalApiKey { get; set; } = string.Empty;
}

public sealed class HttpUserDirectoryService : IUserDirectoryService
{
    private readonly HttpClient _http;
    private readonly AccountServiceSettings _settings;
    private readonly ILogger<HttpUserDirectoryService> _log;

    public HttpUserDirectoryService(
        HttpClient http,
        Microsoft.Extensions.Options.IOptions<AccountServiceSettings> opts,
        ILogger<HttpUserDirectoryService> log)
    {
        _http = http;
        _settings = opts.Value;
        _log = log;
    }

    private sealed record AccountUserPayload(Guid Id, string Email, string Username, string Status);

    public async Task<IReadOnlyDictionary<Guid, UserSummary>> ResolveAsync(
        IReadOnlyCollection<Guid> userIds,
        CancellationToken ct)
    {
        var result = new Dictionary<Guid, UserSummary>();
        if (userIds.Count == 0) return result;

        foreach (var id in userIds.Distinct())
        {
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, $"/internal/users/{id}");
                req.Headers.Add("X-Internal-Api-Key", _settings.InternalApiKey);
                using var resp = await _http.SendAsync(req, ct);
                if (!resp.IsSuccessStatusCode)
                {
                    _log.LogDebug("Account /internal/users/{Id} → {Status}", id, resp.StatusCode);
                    continue;
                }
                var body = await resp.Content.ReadFromJsonAsync<AccountUserPayload>(cancellationToken: ct);
                if (body is null) continue;
                result[body.Id] = new UserSummary { Id = body.Id, Username = body.Username };
            }
            catch (Exception ex)
            {
                _log.LogDebug(ex, "Account lookup failed for {Id}", id);
            }
        }
        return result;
    }
}
