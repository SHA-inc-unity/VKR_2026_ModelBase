using AccountService.Application.DTOs.Requests;
using AccountService.Application.DTOs.Responses;

namespace AccountService.Application.Interfaces.Services;

public interface IAccountService
{
    Task<AuthResponse> RegisterAsync(RegisterRequest request, string? ipAddress = null, string? userAgent = null, CancellationToken ct = default);
    Task<AuthResponse> LoginAsync(LoginRequest request, string? ipAddress = null, string? userAgent = null, CancellationToken ct = default);
    Task<AuthResponse> RefreshAsync(RefreshTokenRequest request, string? ipAddress = null, string? userAgent = null, CancellationToken ct = default);
    Task LogoutAsync(LogoutRequest request, Guid userId, CancellationToken ct = default);
    Task<UserProfileResponse> GetCurrentUserAsync(Guid userId, CancellationToken ct = default);
    Task<UserProfileResponse> UpdateProfileAsync(Guid userId, UpdateProfileRequest request, CancellationToken ct = default);
    Task<UserSettingsResponse> GetSettingsAsync(Guid userId, CancellationToken ct = default);
    Task<UserSettingsResponse> UpdateSettingsAsync(Guid userId, UpdateSettingsRequest request, CancellationToken ct = default);
    Task<InternalUserResponse> GetInternalUserAsync(Guid userId, CancellationToken ct = default);
    Task<InternalUserResponse?> GetInternalUserByEmailAsync(string email, CancellationToken ct = default);
}
