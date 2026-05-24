using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;
using NotificationService.Application.Common.Settings;

namespace NotificationService.API.Services;

public interface IJwtTokenValidator
{
    Guid? ResolveUserId(string token);
}

public sealed class JwtTokenValidator : IJwtTokenValidator
{
    private readonly TokenValidationParameters _params;
    private readonly JwtSecurityTokenHandler _handler = new();

    public JwtTokenValidator(IOptions<JwtSettings> opts)
    {
        var s = opts.Value;
        _params = new TokenValidationParameters
        {
            ValidateIssuerSigningKey = true,
            IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(s.SecretKey)),
            ValidateIssuer = true,
            ValidIssuer = s.Issuer,
            ValidateAudience = true,
            ValidAudience = s.Audience,
            ValidateLifetime = true,
            ClockSkew = TimeSpan.FromSeconds(30),
        };
    }

    public Guid? ResolveUserId(string token)
    {
        try
        {
            var principal = _handler.ValidateToken(token, _params, out _);
            var sub = principal.FindFirstValue(ClaimTypes.NameIdentifier)
                   ?? principal.FindFirstValue("sub")
                   ?? principal.FindFirstValue("nameid");
            return Guid.TryParse(sub, out var g) ? g : null;
        }
        catch
        {
            return null;
        }
    }
}
