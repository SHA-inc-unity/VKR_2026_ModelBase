namespace AccountService.Application.DTOs.Responses;

/// <summary>UI-safe view of a stored API key — never exposes cleartext.</summary>
public sealed record ApiKeyResponse
{
    public Guid Id { get; init; }
    public string Exchange { get; init; } = string.Empty;
    public string Label { get; init; } = string.Empty;
    public string ApiKeyMasked { get; init; } = string.Empty;
    public bool CanRead { get; init; }
    public bool CanTrade { get; init; }
    public string Status { get; init; } = "active";
    public DateTime CreatedAt { get; init; }
    public DateTime? LastUsedAt { get; init; }
    public string? LastValidationError { get; init; }
}

/// <summary>Decrypted payload returned only on the internal endpoint, never publicly.</summary>
public sealed record InternalApiKeyResponse
{
    public Guid Id { get; init; }
    public Guid UserId { get; init; }
    public string Exchange { get; init; } = string.Empty;
    public string ApiKey { get; init; } = string.Empty;
    public string ApiSecret { get; init; } = string.Empty;
    public bool CanRead { get; init; }
    public bool CanTrade { get; init; }
}
