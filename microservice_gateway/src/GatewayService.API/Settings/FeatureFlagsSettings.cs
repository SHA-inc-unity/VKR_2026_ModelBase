namespace GatewayService.API.Settings;

public sealed class FeatureFlagsSettings
{
    public const string SectionName = "FeatureFlags";

    public bool Portfolio { get; init; } = false;
    public bool Market { get; init; } = false;
    public bool News { get; init; } = false;
    public bool Notifications { get; init; } = false;
}
