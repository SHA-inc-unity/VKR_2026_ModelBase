namespace GatewayService.API.Settings;

public sealed class CorsSettings
{
    public const string SectionName = "Cors";
    public const string PolicyName = "GatewayBrowserCors";

    public bool AllowAnyOrigin { get; init; } = true;
    public string[] AllowedOrigins { get; init; } = [];
    public int PreflightMaxAgeSeconds { get; init; } = 600;
}