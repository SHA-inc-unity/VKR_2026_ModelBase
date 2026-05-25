using AccountService.Application.DTOs.Requests;
using AccountService.Application.DTOs.Responses;

namespace AccountService.Application.Interfaces.Services;

public interface IExchangeApiKeyService
{
    Task<ApiKeyResponse> CreateAsync(Guid userId, CreateApiKeyRequest request, CancellationToken ct = default);
    Task<IReadOnlyList<ApiKeyResponse>> ListAsync(Guid userId, CancellationToken ct = default);
    Task<bool> RevokeAsync(Guid userId, Guid id, CancellationToken ct = default);
    Task<InternalApiKeyResponse?> GetDecryptedActiveAsync(Guid userId, string exchange, CancellationToken ct = default);
}
