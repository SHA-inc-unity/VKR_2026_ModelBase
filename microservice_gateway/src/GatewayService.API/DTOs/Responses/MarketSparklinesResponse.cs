namespace GatewayService.API.DTOs.Responses;

/// <summary>
/// Response body for GET /api/v1/market/sparklines.
///
/// Batch trend previews: one short close-price series per requested symbol,
/// assembled server-side (reusing the chart service + its cache) so a client
/// renders sparklines for a whole list with a single call instead of N
/// per-row chart requests.
/// </summary>
public sealed record MarketSparklinesResponse
{
    public IReadOnlyList<SparklineDto> Items { get; init; } = [];
}

public sealed record SparklineDto
{
    public string Symbol { get; init; } = string.Empty;

    /// <summary>Close prices in ascending time order (oldest → newest).</summary>
    public IReadOnlyList<decimal> Points { get; init; } = [];
}
