namespace SocialService.Application.DTOs.Requests;

public sealed class SentimentVoteRequest
{
    public string TargetType { get; set; } = string.Empty;
    public string TargetId { get; set; } = string.Empty;

    /// <summary>"bullish" | "bearish" | "none" (none retracts the vote).</summary>
    public string Vote { get; set; } = string.Empty;
}
