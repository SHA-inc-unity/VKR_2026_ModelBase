using AccountService.Application.Crypto;
using AccountService.Application.DTOs.Requests;
using AccountService.Application.DTOs.Responses;
using AccountService.Application.Interfaces.Repositories;
using AccountService.Application.Interfaces.Services;
using AccountService.Domain.Entities;

namespace AccountService.Application.Services;

public sealed class ExchangeApiKeyService : IExchangeApiKeyService
{
    private static readonly HashSet<string> SupportedExchanges =
        new(StringComparer.OrdinalIgnoreCase) { "bybit" }; // binance later

    private readonly IExchangeApiKeyRepository _repo;
    private readonly IAesGcmEncryption _crypto;

    public ExchangeApiKeyService(IExchangeApiKeyRepository repo, IAesGcmEncryption crypto)
    {
        _repo = repo;
        _crypto = crypto;
    }

    public async Task<ApiKeyResponse> CreateAsync(Guid userId, CreateApiKeyRequest request, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(request.ApiKey) || string.IsNullOrWhiteSpace(request.ApiSecret))
            throw new ArgumentException("apiKey and apiSecret are required");

        var exchange = (request.Exchange ?? "").Trim().ToLowerInvariant();
        if (!SupportedExchanges.Contains(exchange))
            throw new ArgumentException($"Exchange '{exchange}' is not supported yet");

        if (request.CanTrade)
            throw new ArgumentException("Trade-permission keys are not enabled yet");

        var entity = new ExchangeApiKey
        {
            Id = Guid.NewGuid(),
            UserId = userId,
            Exchange = exchange,
            Label = string.IsNullOrWhiteSpace(request.Label) ? "main" : request.Label.Trim(),
            ApiKeyEnc = _crypto.Encrypt(request.ApiKey.Trim()),
            ApiSecretEnc = _crypto.Encrypt(request.ApiSecret.Trim()),
            ApiKeyMasked = Mask(request.ApiKey.Trim()),
            CanRead = true,
            CanTrade = false,
            CreatedAt = DateTime.UtcNow,
            Status = "active",
        };

        await _repo.AddAsync(entity, ct);
        await _repo.SaveChangesAsync(ct);

        return ToDto(entity);
    }

    public async Task<IReadOnlyList<ApiKeyResponse>> ListAsync(Guid userId, CancellationToken ct = default)
    {
        var items = await _repo.ListAsync(userId, ct);
        return items.Select(ToDto).ToList();
    }

    public async Task<bool> RevokeAsync(Guid userId, Guid id, CancellationToken ct = default)
    {
        var entity = await _repo.GetByIdAsync(userId, id, ct);
        if (entity is null) return false;
        entity.Status = "revoked";
        await _repo.SaveChangesAsync(ct);
        return true;
    }

    public async Task<InternalApiKeyResponse?> GetDecryptedActiveAsync(Guid userId, string exchange, CancellationToken ct = default)
    {
        var entity = await _repo.GetActiveForExchangeAsync(userId, exchange.Trim().ToLowerInvariant(), ct);
        if (entity is null) return null;

        entity.LastUsedAt = DateTime.UtcNow;
        await _repo.SaveChangesAsync(ct);

        return new InternalApiKeyResponse
        {
            Id = entity.Id,
            UserId = entity.UserId,
            Exchange = entity.Exchange,
            ApiKey = _crypto.Decrypt(entity.ApiKeyEnc),
            ApiSecret = _crypto.Decrypt(entity.ApiSecretEnc),
            CanRead = entity.CanRead,
            CanTrade = entity.CanTrade,
        };
    }

    private static string Mask(string apiKey)
    {
        if (apiKey.Length <= 8) return new string('•', apiKey.Length);
        return $"{apiKey[..4]}••••{apiKey[^4..]}";
    }

    private static ApiKeyResponse ToDto(ExchangeApiKey e) => new()
    {
        Id = e.Id,
        Exchange = e.Exchange,
        Label = e.Label,
        ApiKeyMasked = e.ApiKeyMasked,
        CanRead = e.CanRead,
        CanTrade = e.CanTrade,
        Status = e.Status,
        CreatedAt = e.CreatedAt,
        LastUsedAt = e.LastUsedAt,
        LastValidationError = e.LastValidationError,
    };
}
