namespace GatewayService.API.Settings;

public sealed class DownstreamServicesSettings
{
    public const string SectionName = "DownstreamServices";

    public ServiceEndpointSettings Portfolio { get; init; } = new();
    public ServiceEndpointSettings Market { get; init; } = new();
    public ServiceEndpointSettings News { get; init; } = new();
    public ServiceEndpointSettings Notifications { get; init; } = new();
}

public sealed class ServiceEndpointSettings
{
    public string BaseUrl { get; init; } = string.Empty;
    public bool Enabled { get; init; } = true;
}
