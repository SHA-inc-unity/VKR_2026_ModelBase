namespace AccountService.Application.DTOs.Requests;

public sealed record CreateApiKeyRequest
{
    /// <summary>"bybit" | "binance"; case-insensitive.</summary>
    public string Exchange { get; init; } = string.Empty;
    public string Label { get; init; } = string.Empty;
    public string ApiKey { get; init; } = string.Empty;
    public string ApiSecret { get; init; } = string.Empty;
    /// <summary>UI does not allow this yet; controller rejects true.</summary>
    public bool CanTrade { get; init; } = false;
}
