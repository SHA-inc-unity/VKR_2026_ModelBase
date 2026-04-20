using AccountService.Application.Common.Exceptions;
using AccountService.Application.Common.Settings;
using AccountService.Application.DTOs.Requests;
using AccountService.Application.Interfaces.Cache;
using AccountService.Application.Interfaces.Repositories;
using AccountService.Application.Interfaces.Services;
using AccountService.Application.Services;
using AccountService.Domain.Entities;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Moq;
using Xunit;

namespace AccountService.UnitTests.Services;

public sealed class AccountAppServiceTests
{
    private readonly Mock<IUserRepository> _userRepo = new();
    private readonly Mock<IRoleRepository> _roleRepo = new();
    private readonly Mock<IRefreshTokenRepository> _tokenRepo = new();
    private readonly Mock<ITokenService> _tokenService = new();
    private readonly Mock<IPasswordService> _passwordService = new();
    private readonly Mock<ITokenCacheService> _tokenCache = new();
    private readonly AccountAppService _sut;

    public AccountAppServiceTests()
    {
        _sut = new AccountAppService(
            _userRepo.Object,
            _roleRepo.Object,
            _tokenRepo.Object,
            _tokenService.Object,
            _passwordService.Object,
            _tokenCache.Object,
            NullLogger<AccountAppService>.Instance);

        // Defaults
        _passwordService.Setup(p => p.ValidateStrength(It.IsAny<string>())).Returns((string?)null);
        _passwordService.Setup(p => p.Hash(It.IsAny<string>())).Returns("hashed");
        _tokenService.Setup(t => t.GenerateAccessToken(It.IsAny<User>(), It.IsAny<IEnumerable<string>>()))
            .Returns("access-token");
        _tokenService.Setup(t => t.GenerateRefreshToken()).Returns(("raw-token", "token-hash"));
        _tokenService.Setup(t => t.AccessTokenExpiration).Returns(TimeSpan.FromMinutes(15));
        _tokenService.Setup(t => t.RefreshTokenExpiration).Returns(TimeSpan.FromDays(30));
        _roleRepo.Setup(r => r.GetUserRoleCodesAsync(It.IsAny<Guid>(), default)).ReturnsAsync(["user"]);
        _tokenRepo.Setup(r => r.AddAsync(It.IsAny<RefreshToken>(), default)).Returns(Task.CompletedTask);
        _tokenRepo.Setup(r => r.SaveChangesAsync(default)).Returns(Task.CompletedTask);
    }

    [Fact]
    public async Task RegisterAsync_HappyPath_ReturnsAuthResponse()
    {
        _userRepo.Setup(r => r.EmailExistsAsync("test@test.com", default)).ReturnsAsync(false);
        _userRepo.Setup(r => r.UsernameExistsAsync("testuser", default)).ReturnsAsync(false);
        _userRepo.Setup(r => r.AddAsync(It.IsAny<User>(), default)).Returns(Task.CompletedTask);
        _userRepo.Setup(r => r.AddSettingsAsync(It.IsAny<UserSettings>(), default)).Returns(Task.CompletedTask);
        _userRepo.Setup(r => r.SaveChangesAsync(default)).Returns(Task.CompletedTask);
        _roleRepo.Setup(r => r.AssignRoleAsync(It.IsAny<Guid>(), "user", default)).Returns(Task.CompletedTask);

        var request = new RegisterRequest("test@test.com", "testuser", "Password1");
        var result = await _sut.RegisterAsync(request);

        result.AccessToken.Should().Be("access-token");
        result.RefreshToken.Should().Be("raw-token");
        result.User.Email.Should().Be("test@test.com");
    }

    [Fact]
    public async Task RegisterAsync_EmailAlreadyExists_ThrowsEmailAlreadyExistsException()
    {
        _userRepo.Setup(r => r.EmailExistsAsync("test@test.com", default)).ReturnsAsync(true);

        var request = new RegisterRequest("test@test.com", "testuser", "Password1");
        var act = async () => await _sut.RegisterAsync(request);

        await act.Should().ThrowAsync<EmailAlreadyExistsException>();
    }

    [Fact]
    public async Task RegisterAsync_WeakPassword_ThrowsWeakPasswordException()
    {
        _passwordService.Setup(p => p.ValidateStrength(It.IsAny<string>())).Returns("too short");

        var request = new RegisterRequest("test@test.com", "testuser", "weak");
        var act = async () => await _sut.RegisterAsync(request);

        await act.Should().ThrowAsync<WeakPasswordException>();
    }

    [Fact]
    public async Task LoginAsync_WrongPassword_ThrowsInvalidCredentialsException()
    {
        var user = User.Create("test@test.com", "testuser", "hashed");
        _userRepo.Setup(r => r.GetByEmailAsync("test@test.com", default)).ReturnsAsync(user);
        _passwordService.Setup(p => p.Verify("wrongpass", "hashed")).Returns(false);

        var request = new LoginRequest("test@test.com", "wrongpass");
        var act = async () => await _sut.LoginAsync(request);

        await act.Should().ThrowAsync<InvalidCredentialsException>();
    }

    [Fact]
    public async Task LoginAsync_UserNotFound_ThrowsInvalidCredentialsException()
    {
        _userRepo.Setup(r => r.GetByEmailAsync(It.IsAny<string>(), default)).ReturnsAsync((User?)null);

        var request = new LoginRequest("nouser@test.com", "pass");
        var act = async () => await _sut.LoginAsync(request);

        await act.Should().ThrowAsync<InvalidCredentialsException>();
    }

    [Fact]
    public async Task LogoutAsync_ValidToken_RevokesToken()
    {
        var userId = Guid.NewGuid();
        var token = RefreshToken.Create(userId, "token-hash", DateTimeOffset.UtcNow.AddDays(30));
        _tokenRepo.Setup(r => r.GetByHashAsync(It.IsAny<string>(), default)).ReturnsAsync(token);
        _tokenRepo.Setup(r => r.RevokeAsync(token.Id, default)).Returns(Task.CompletedTask);

        var request = new LogoutRequest("raw-refresh-token");
        await _sut.LogoutAsync(request, userId);

        _tokenRepo.Verify(r => r.RevokeAsync(token.Id, default), Times.Once);
    }
}
