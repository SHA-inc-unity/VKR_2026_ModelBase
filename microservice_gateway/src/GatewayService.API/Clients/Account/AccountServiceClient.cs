using System.IdentityModel.Tokens.Jwt;
using System.Text.Json;
using GatewayService.API.Clients.Account.Dtos;
using GatewayService.API.Common;
using GatewayService.API.Kafka;

namespace GatewayService.API.Clients.Account;

/// <summary>
/// Kafka-backed client for microservice_account. Sends a `cmd.account.get_user`
/// request (user_id extracted from the bearer JWT) and parses the reply envelope.
/// </summary>
public sealed class AccountServiceClient : IAccountServiceClient
{
    private static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(5);
    private static readonly JwtSecurityTokenHandler TokenHandler = new();

    private readonly KafkaRequestClient _kafka;
    private readonly ILogger<AccountServiceClient> _logger;

    public AccountServiceClient(KafkaRequestClient kafka, ILogger<AccountServiceClient> logger)
    {
        _kafka  = kafka;
        _logger = logger;
    }

    public async Task<ServiceResult<AccountUserDto>> GetCurrentUserAsync(
        string bearerToken, CancellationToken ct = default)
    {
        var userId = ExtractUserId(bearerToken);
        if (userId is null)
            return ServiceResult<AccountUserDto>.Fail("Bearer token has no user id claim");

        JsonElement reply;
        try
        {
            reply = await _kafka.RequestAsync(
                Topics.CmdAccountGetUser,
                new { user_id = userId },
                RequestTimeout,
                ct);
        }
        catch (TimeoutException ex)
        {
            _logger.LogWarning(ex, "Account Kafka request timed out");
            return ServiceResult<AccountUserDto>.Fail("Account service timeout");
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Account Kafka request failed");
            return ServiceResult<AccountUserDto>.Fail(ex.Message);
        }

        if (reply.ValueKind == JsonValueKind.Object &&
            reply.TryGetProperty("error", out var errEl))
        {
            return ServiceResult<AccountUserDto>.Fail(errEl.GetString() ?? "account error");
        }

        var dto = ParseUser(reply);
        return dto is not null
            ? ServiceResult<AccountUserDto>.Ok(dto)
            : ServiceResult<AccountUserDto>.Fail("Account service returned invalid payload");
    }

    private static string? ExtractUserId(string bearerToken)
    {
        try
        {
            var jwt = TokenHandler.ReadJwtToken(bearerToken);
            var sub = jwt.Claims.FirstOrDefault(c => c.Type is "sub" or "nameid" or "nameidentifier");
            return sub?.Value;
        }
        catch
        {
            return null;
        }
    }

    private static AccountUserDto? ParseUser(JsonElement el)
    {
        if (el.ValueKind != JsonValueKind.Object) return null;

        try
        {
            var id       = el.TryGetProperty("id", out var idEl)           ? idEl.GetGuid()       : Guid.Empty;
            var email    = el.TryGetProperty("email", out var em)          ? em.GetString() ?? "" : "";
            var username = el.TryGetProperty("username", out var un)       ? un.GetString() ?? "" : "";
            var status   = el.TryGetProperty("status", out var st)         ? st.GetString() ?? "" : "";

            var roles = new List<string>();
            if (el.TryGetProperty("roles", out var rolesEl) && rolesEl.ValueKind == JsonValueKind.Array)
            {
                foreach (var r in rolesEl.EnumerateArray())
                    if (r.ValueKind == JsonValueKind.String && r.GetString() is { } s) roles.Add(s);
            }

            var createdAt = el.TryGetProperty("created_at", out var ca) && ca.TryGetDateTimeOffset(out var cao)
                ? cao
                : default;

            return new AccountUserDto
            {
                Id        = id,
                Email     = email,
                Username  = username,
                Status    = status,
                Roles     = roles,
                CreatedAt = createdAt,
                UpdatedAt = createdAt,
            };
        }
        catch
        {
            return null;
        }
    }
}
