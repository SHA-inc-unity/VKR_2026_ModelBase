using AccountService.Domain.Entities;

namespace AccountService.Application.Interfaces.Services;

public interface ITokenService
{
    /// <summary>Generates a short-lived JWT access token with user claims and roles.</summary>
    string GenerateAccessToken(User user, IEnumerable<string> roles);

    /// <summary>Generates a cryptographically random refresh token. Returns (rawToken, sha256Hash).</summary>
    (string RawToken, string TokenHash) GenerateRefreshToken();

    /// <summary>Returns the JTI claim from a (possibly expired) token, or null if malformed.</summary>
    string? GetJtiFromToken(string token);

    /// <summary>Returns the user ID from a (possibly expired) token, or null.</summary>
    Guid? GetUserIdFromToken(string token);

    /// <summary>The TTL of the access token.</summary>
    TimeSpan AccessTokenExpiration { get; }

    /// <summary>The TTL of the refresh token.</summary>
    TimeSpan RefreshTokenExpiration { get; }
}
