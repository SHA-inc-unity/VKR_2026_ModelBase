using System.Security.Cryptography;
using System.Text;
using AccountService.Application.Common.Exceptions;
using AccountService.Application.DTOs.Requests;
using AccountService.Application.DTOs.Responses;
using AccountService.Application.Interfaces.Cache;
using AccountService.Application.Interfaces.Repositories;
using AccountService.Application.Interfaces.Services;
using AccountService.Domain.Entities;
using Microsoft.Extensions.Logging;

namespace AccountService.Application.Services;

public sealed class AccountAppService : IAccountService
{
    private readonly IUserRepository _userRepo;
    private readonly IRoleRepository _roleRepo;
    private readonly IRefreshTokenRepository _tokenRepo;
    private readonly ITokenService _tokenService;
    private readonly IPasswordService _passwordService;
    private readonly ITokenCacheService _tokenCache;
    private readonly ILogger<AccountAppService> _logger;

    public AccountAppService(
        IUserRepository userRepo,
        IRoleRepository roleRepo,
        IRefreshTokenRepository tokenRepo,
        ITokenService tokenService,
        IPasswordService passwordService,
        ITokenCacheService tokenCache,
        ILogger<AccountAppService> logger)
    {
        _userRepo = userRepo;
        _roleRepo = roleRepo;
        _tokenRepo = tokenRepo;
        _tokenService = tokenService;
        _passwordService = passwordService;
        _tokenCache = tokenCache;
        _logger = logger;
    }

    // ── Register ────────────────────────────────────────────────────────────
    public async Task<AuthResponse> RegisterAsync(
        RegisterRequest request,
        string? ipAddress = null,
        string? userAgent = null,
        CancellationToken ct = default)
    {
        var strengthError = _passwordService.ValidateStrength(request.Password);
        if (strengthError is not null)
            throw new WeakPasswordException(strengthError);

        if (await _userRepo.EmailExistsAsync(request.Email, ct))
            throw new EmailAlreadyExistsException(request.Email);

        if (await _userRepo.UsernameExistsAsync(request.Username, ct))
            throw new UsernameAlreadyExistsException(request.Username);

        var hash = _passwordService.Hash(request.Password);
        var user = User.Create(request.Email, request.Username, hash);
        await _userRepo.AddAsync(user, ct);

        var settings = UserSettings.CreateDefault(user.Id);
        await _userRepo.AddSettingsAsync(settings, ct);
        await _roleRepo.AssignRoleAsync(user.Id, Role.Codes.User, ct);
        await _userRepo.SaveChangesAsync(ct);

        var roles = await _roleRepo.GetUserRoleCodesAsync(user.Id, ct);
        var (accessToken, rawRefresh, tokenRecord) = await IssueTokensAsync(user, roles, null, ipAddress, userAgent, ct);

        _logger.LogInformation("User {UserId} registered successfully", user.Id);

        return BuildAuthResponse(accessToken, rawRefresh, tokenRecord, user, roles);
    }

    // ── Login ───────────────────────────────────────────────────────────────
    public async Task<AuthResponse> LoginAsync(
        LoginRequest request,
        string? ipAddress = null,
        string? userAgent = null,
        CancellationToken ct = default)
    {
        var user = await _userRepo.GetByEmailAsync(request.Email, ct);

        if (user is null || !_passwordService.Verify(request.Password, user.PasswordHash))
        {
            if (user is not null)
                _logger.LogWarning("Failed login attempt for user {UserId}", user.Id);
            throw new InvalidCredentialsException();
        }

        if (!user.IsActive)
            throw new InvalidCredentialsException();

        var roles = await _roleRepo.GetUserRoleCodesAsync(user.Id, ct);
        var (accessToken, rawRefresh, tokenRecord) = await IssueTokensAsync(user, roles, request.DeviceId, ipAddress, userAgent, ct);

        _logger.LogInformation("User {UserId} logged in", user.Id);

        return BuildAuthResponse(accessToken, rawRefresh, tokenRecord, user, roles);
    }

    // ── Refresh ─────────────────────────────────────────────────────────────
    public async Task<AuthResponse> RefreshAsync(
        RefreshTokenRequest request,
        string? ipAddress = null,
        string? userAgent = null,
        CancellationToken ct = default)
    {
        var tokenHash = HashToken(request.RefreshToken);
        var stored = await _tokenRepo.GetByHashAsync(tokenHash, ct);

        if (stored is null) throw TokenException.Invalid();
        if (stored.IsRevoked) throw TokenException.Revoked();
        if (stored.IsExpired) throw TokenException.Expired();

        await _tokenRepo.RevokeAsync(stored.Id, ct);

        var user = await _userRepo.GetByIdWithRolesAsync(stored.UserId, ct)
            ?? throw new UserNotFoundException(stored.UserId);

        // user.UserRoles is already populated by the Include() in
        // GetByIdWithRolesAsync — re-querying via _roleRepo.GetUserRoleCodesAsync
        // adds an unnecessary roundtrip on the refresh path.
        var roles = ExtractRoleCodes(user);
        var (accessToken, rawRefresh, tokenRecord) = await IssueTokensAsync(user, roles, stored.DeviceId, ipAddress, userAgent, ct);

        _logger.LogInformation("Tokens refreshed for user {UserId}", user.Id);

        return BuildAuthResponse(accessToken, rawRefresh, tokenRecord, user, roles);
    }

    // ── Logout ──────────────────────────────────────────────────────────────
    public async Task LogoutAsync(
        LogoutRequest request,
        Guid userId,
        CancellationToken ct = default)
    {
        var tokenHash = HashToken(request.RefreshToken);
        var stored = await _tokenRepo.GetByHashAsync(tokenHash, ct);

        if (stored is not null && stored.UserId == userId)
            await _tokenRepo.RevokeAsync(stored.Id, ct);

        await _tokenRepo.SaveChangesAsync(ct);
        _logger.LogInformation("User {UserId} logged out", userId);
    }

    // ── Me / Profile ────────────────────────────────────────────────────────
    public async Task<UserProfileResponse> GetCurrentUserAsync(Guid userId, CancellationToken ct = default)
    {
        var user = await _userRepo.GetByIdWithRolesAsync(userId, ct)
            ?? throw new UserNotFoundException(userId);
        // Roles already eagerly loaded via Include() — no second roundtrip.
        return ToProfileResponse(user, ExtractRoleCodes(user));
    }

    public async Task<UserProfileResponse> UpdateProfileAsync(
        Guid userId,
        UpdateProfileRequest request,
        CancellationToken ct = default)
    {
        // GetByIdAsync stays tracked (user mutates here). Roles are not on
        // the tracked entity, so we still need one query for them.
        var user = await _userRepo.GetByIdAsync(userId, ct)
            ?? throw new UserNotFoundException(userId);

        if (request.Username is not null && request.Username != user.Username)
        {
            if (await _userRepo.UsernameExistsAsync(request.Username, ct))
                throw new UsernameAlreadyExistsException(request.Username);
        }

        user.UpdateProfile(request.Username);
        await _userRepo.UpdateAsync(user, ct);
        await _userRepo.SaveChangesAsync(ct);

        var roles = await _roleRepo.GetUserRoleCodesAsync(userId, ct);
        return ToProfileResponse(user, roles);
    }

    // ── Settings ────────────────────────────────────────────────────────────
    public async Task<UserSettingsResponse> GetSettingsAsync(Guid userId, CancellationToken ct = default)
    {
        _ = await _userRepo.GetByIdAsync(userId, ct)
            ?? throw new UserNotFoundException(userId);

        var settings = await EnsureSettingsExistAsync(userId, ct);
        return ToSettingsResponse(settings);
    }

    public async Task<UserSettingsResponse> UpdateSettingsAsync(
        Guid userId,
        UpdateSettingsRequest request,
        CancellationToken ct = default)
    {
        _ = await _userRepo.GetByIdAsync(userId, ct)
            ?? throw new UserNotFoundException(userId);

        var settings = await EnsureSettingsExistAsync(userId, ct);
        settings.Update(request.Theme, request.Locale, request.NotificationsEnabled);
        await _userRepo.SaveChangesAsync(ct);

        return ToSettingsResponse(settings);
    }

    // ── Internal ────────────────────────────────────────────────────────────
    public async Task<InternalUserResponse> GetInternalUserAsync(Guid userId, CancellationToken ct = default)
    {
        var user = await _userRepo.GetByIdWithRolesAsync(userId, ct)
            ?? throw new UserNotFoundException(userId);
        // Roles already loaded via Include().
        var roles = ExtractRoleCodes(user);
        return new InternalUserResponse(user.Id, user.Email, user.Username, user.Status.ToString(), roles);
    }

    public async Task<InternalUserResponse?> GetInternalUserByEmailAsync(string email, CancellationToken ct = default)
    {
        var user = await _userRepo.GetByEmailAsync(email, ct);
        if (user is null) return null;
        var roles = await _roleRepo.GetUserRoleCodesAsync(user.Id, ct);
        return new InternalUserResponse(user.Id, user.Email, user.Username, user.Status.ToString(), roles);
    }

    // ── Private helpers ──────────────────────────────────────────────────────

    private async Task<(string accessToken, string rawRefresh, RefreshToken record)> IssueTokensAsync(
        User user,
        IReadOnlyList<string> roles,
        string? deviceId,
        string? ipAddress,
        string? userAgent,
        CancellationToken ct)
    {
        var accessToken = _tokenService.GenerateAccessToken(user, roles);
        var (rawRefresh, refreshHash) = _tokenService.GenerateRefreshToken();
        var expiresAt = DateTimeOffset.UtcNow.Add(_tokenService.RefreshTokenExpiration);

        var tokenRecord = RefreshToken.Create(user.Id, refreshHash, expiresAt, deviceId, ipAddress, userAgent);
        await _tokenRepo.AddAsync(tokenRecord, ct);
        await _tokenRepo.SaveChangesAsync(ct);

        return (accessToken, rawRefresh, tokenRecord);
    }

    private AuthResponse BuildAuthResponse(
        string accessToken,
        string rawRefreshToken,
        RefreshToken tokenRecord,
        User user,
        IReadOnlyList<string> roles) =>
        new(
            AccessToken: accessToken,
            RefreshToken: rawRefreshToken,
            AccessTokenExpiresAt: DateTimeOffset.UtcNow.Add(_tokenService.AccessTokenExpiration),
            RefreshTokenExpiresAt: tokenRecord.ExpiresAt,
            User: ToProfileResponse(user, roles)
        );

    private static UserProfileResponse ToProfileResponse(User user, IReadOnlyList<string> roles) =>
        new(user.Id, user.Email, user.Username, user.Status.ToString(), roles, user.CreatedAt, user.UpdatedAt);

    private static UserSettingsResponse ToSettingsResponse(UserSettings s) =>
        new(s.Theme, s.Locale, s.NotificationsEnabled, s.UpdatedAt);

    /// <summary>Fetches settings; creates defaults if they don't exist (defensive guard).</summary>
    private async Task<UserSettings> EnsureSettingsExistAsync(Guid userId, CancellationToken ct)
    {
        var settings = await _userRepo.GetSettingsAsync(userId, ct);
        if (settings is not null) return settings;

        settings = UserSettings.CreateDefault(userId);
        await _userRepo.AddSettingsAsync(settings, ct);
        await _userRepo.SaveChangesAsync(ct);
        return settings;
    }

    private static string HashToken(string rawToken)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(rawToken));
        return Convert.ToBase64String(bytes);
    }

    /// <summary>
    /// Project the eagerly-loaded UserRoles navigation onto a flat list of
    /// role codes. Used when <see cref="IUserRepository.GetByIdWithRolesAsync"/>
    /// has already populated the graph — saves one extra query per call.
    /// </summary>
    private static IReadOnlyList<string> ExtractRoleCodes(User user)
    {
        var ur = user.UserRoles;
        if (ur is null) return Array.Empty<string>();
        var result = new List<string>(ur.Count);
        foreach (var link in ur)
        {
            var code = link.Role?.Code;
            if (!string.IsNullOrEmpty(code)) result.Add(code);
        }
        return result;
    }
}
