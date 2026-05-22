using AccountService.Application.Common.Settings;
using AccountService.Application.Services;
using FluentAssertions;
using Microsoft.Extensions.Options;
using Xunit;

namespace AccountService.UnitTests.Services;

public sealed class PasswordServiceTests
{
    private readonly PasswordService _sut;

    public PasswordServiceTests()
    {
        var settings = Options.Create(new PasswordSettings
        {
            MinLength = 8,
            RequireUppercase = true,
            RequireLowercase = true,
            RequireDigit = true,
            RequireSpecialChar = false,
            WorkFactor = 4 // low for tests
        });
        _sut = new PasswordService(settings);
    }

    [Fact]
    public void Hash_ReturnsNonEmptyString()
    {
        var hash = _sut.Hash("Password1");
        hash.Should().NotBeNullOrEmpty();
    }

    [Fact]
    public void Hash_TwiceSamePassword_ReturnsDifferentHashes()
    {
        var h1 = _sut.Hash("Password1");
        var h2 = _sut.Hash("Password1");
        h1.Should().NotBe(h2); // BCrypt uses random salt
    }

    [Fact]
    public void Verify_CorrectPassword_ReturnsTrue()
    {
        var hash = _sut.Hash("Password1");
        _sut.Verify("Password1", hash).Should().BeTrue();
    }

    [Fact]
    public void Verify_WrongPassword_ReturnsFalse()
    {
        var hash = _sut.Hash("Password1");
        _sut.Verify("WrongPass1", hash).Should().BeFalse();
    }

    [Theory]
    [InlineData("short1A", "minimum length")]       // too short
    [InlineData("allowercase1", "uppercase")]        // no uppercase
    [InlineData("ALLUPPERCASE1", "lowercase")]       // no lowercase
    [InlineData("NoDigitsHere", "digit")]            // no digit
    public void ValidateStrength_WeakPassword_ReturnsErrorMessage(string password, string expectedContains)
    {
        var result = _sut.ValidateStrength(password);
        result.Should().NotBeNull();
        result!.Should().Contain(expectedContains);
    }

    [Theory]
    [InlineData("StrongPass1")]
    [InlineData("AnotherValid2")]
    [InlineData("Passw0rd")]
    public void ValidateStrength_StrongPassword_ReturnsNull(string password)
    {
        var result = _sut.ValidateStrength(password);
        result.Should().BeNull();
    }
}
