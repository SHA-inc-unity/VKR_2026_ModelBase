namespace GatewayService.API.Settings;

public sealed class CorsSettings
{
    public const string SectionName = "Cors";
    public const string PolicyName = "GatewayBrowserCors";

    // Default to a closed allow-list (browser cross-origin only — native apps
    // send no Origin and aren't affected). Override via the Cors config section.
    public bool AllowAnyOrigin { get; init; } = false;
    public string[] AllowedOrigins { get; init; } =
    [
        "https://sha-trade.tech",
        "https://www.sha-trade.tech",
    ];
    public int PreflightMaxAgeSeconds { get; init; } = 600;
}