using System.IdentityModel.Tokens.Jwt;
using AccountService.Application.Common.Settings;
using AccountService.Application.Services;
using AccountService.Domain.Entities;
using AccountService.Domain.Enums;
using FluentAssertions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AccountService.UnitTests.Services;

public sealed class TokenServiceTests
{
    private readonly TokenService _sut;

    public TokenServiceTests()
    {
        var settings = Options.Create(new JwtSettings
        {
            SecretKey = "test-secret-key-that-is-long-enough-32c",
            Issuer = "test-issuer",
            Audience = "test-audience",
            AccessTokenExpirationMinutes = 15,
            RefreshTokenExpirationDays = 30
        });
        _sut = new TokenService(settings);
    }

    [Fact]
    public void GenerateAccessToken_ContainsExpectedClaims()
    {
        var user = User.Create("test@example.com", "testuser", "hash");
        var roles = new[] { "user" };

        var token = _sut.GenerateAccessToken(user, roles);

        var handler = new JwtSecurityTokenHandler();
        var jwt = handler.ReadJwtToken(token);

        jwt.Subject.Should().Be(user.Id.ToString());
        jwt.Claims.Should().Contain(c => c.Type == JwtRegisteredClaimNames.Email && c.Value == user.Email);
        jwt.Claims.Should().Contain(c => c.Value == "user"); // role claim
        jwt.Id.Should().NotBeNullOrEmpty();
        jwt.Issuer.Should().Be("test-issuer");
    }

    [Fact]
    public void GenerateRefreshToken_ReturnsDifferentTokensEachCall()
    {
        var (raw1, hash1) = _sut.GenerateRefreshToken();
        var (raw2, hash2) = _sut.GenerateRefreshToken();

        raw1.Should().NotBe(raw2);
        hash1.Should().NotBe(hash2);
    }

    [Fact]
    public void GenerateRefreshToken_HashIsNotSameAsRaw()
    {
        var (raw, hash) = _sut.GenerateRefreshToken();
        raw.Should().NotBe(hash);
        hash.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public void GetJtiFromToken_ExtractsCorrectJti()
    {
        var user = User.Create("test@example.com", "testuser", "hash");
        var token = _sut.GenerateAccessToken(user, ["user"]);

        var jti = _sut.GetJtiFromToken(token);

        jti.Should().NotBeNullOrEmpty();
        Guid.TryParse(jti, out _).Should().BeTrue(); // N-format (no hyphens) is still a valid Guid
    }

    [Fact]
    public void GetUserIdFromToken_ExtractsCorrectUserId()
    {
        var user = User.Create("test@example.com", "testuser", "hash");
        var token = _sut.GenerateAccessToken(user, []);

        var userId = _sut.GetUserIdFromToken(token);

        userId.Should().Be(user.Id);
    }

    [Fact]
    public void GetJtiFromToken_InvalidToken_ReturnsNull()
    {
        var result = _sut.GetJtiFromToken("not.a.jwt");
        result.Should().BeNull();
    }
}
