namespace GatewayService.API.DTOs.Responses;

public sealed record NewsListResponse
{
    public IReadOnlyList<NewsItemDto> Items { get; init; } = [];
    public int Total { get; init; }
    public bool Degraded { get; init; }
}

public sealed record NewsItemDto
{
    public string Id { get; init; } = string.Empty;
    public string Title { get; init; } = string.Empty;
    public string Summary { get; init; } = string.Empty;
    public string Source { get; init; } = string.Empty;
    public string? ImageUrl { get; init; }
    public DateTimeOffset PublishedAt { get; init; }
    public IReadOnlyList<string> Tags { get; init; } = [];
}
