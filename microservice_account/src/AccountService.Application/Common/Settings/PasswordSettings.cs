namespace AccountService.Application.Common.Settings;

public sealed class PasswordSettings
{
    public const string SectionName = "Password";

    public int MinLength { get; init; } = 8;
    public bool RequireUppercase { get; init; } = true;
    public bool RequireLowercase { get; init; } = true;
    public bool RequireDigit { get; init; } = true;
    public bool RequireSpecialChar { get; init; } = false;
    public int WorkFactor { get; init; } = 12;
}
