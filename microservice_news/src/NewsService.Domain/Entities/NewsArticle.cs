namespace NewsService.Domain.Entities;

public sealed class NewsArticle
{
    public Guid Id { get; private set; }
    public string Source { get; private set; } = string.Empty;
    public string SourceUrl { get; private set; } = string.Empty;
    public string Title { get; private set; } = string.Empty;
    public string Summary { get; private set; } = string.Empty;
    public string? ImageUrl { get; private set; }
    public DateTime PublishedAt { get; private set; }
    public string[] Tags { get; private set; } = Array.Empty<string>();
    public DateTime IngestedAt { get; private set; }

    private NewsArticle() { }

    public static NewsArticle Create(
        string source,
        string sourceUrl,
        string title,
        string summary,
        string? imageUrl,
        DateTime publishedAt,
        IEnumerable<string> tags)
    {
        return new NewsArticle
        {
            Id = Guid.NewGuid(),
            Source = source.Trim(),
            SourceUrl = sourceUrl.Trim(),
            Title = title.Trim(),
            Summary = (summary ?? string.Empty).Trim(),
            ImageUrl = string.IsNullOrWhiteSpace(imageUrl) ? null : imageUrl.Trim(),
            PublishedAt = publishedAt.ToUniversalTime(),
            Tags = (tags ?? Enumerable.Empty<string>())
                .Where(t => !string.IsNullOrWhiteSpace(t))
                .Select(t => t.Trim().ToUpperInvariant())
                .Distinct()
                .ToArray(),
            IngestedAt = DateTime.UtcNow,
        };
    }
}
