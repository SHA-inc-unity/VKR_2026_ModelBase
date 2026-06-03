namespace SocialService.Application.DTOs.Responses;

/// <summary>
/// Community sentiment aggregate for one target. <c>MyVote</c> is
/// "bullish"/"bearish" when the viewer has an active vote, otherwise "none"
/// (also "none" for anonymous viewers).
/// </summary>
public sealed class SentimentResponse
{
    public int Bullish { get; set; }
    public int Bearish { get; set; }
    public int Total { get; set; }
    public string MyVote { get; set; } = "none";
}
