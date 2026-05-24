namespace SocialService.Application.Common.Settings;

public sealed class JwtSettings
{
    public const string SectionName = "Jwt";

    public string SecretKey { get; set; } = string.Empty;
    public string Issuer { get; set; } = "account-service";
    public string Audience { get; set; } = "exchange-app";
}
