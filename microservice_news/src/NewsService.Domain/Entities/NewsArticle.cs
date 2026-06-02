namespace NewsService.Domain.Entities;

public sealed class NewsArticle
{
    public Guid Id { get; private set; }
    public string Source { get; private set; } = string.Empty;
    public string SourceUrl { get; private set; } = string.Empty;
    public string Title { get; private set; } = string.Empty;
    public string Summary { get; private set; } = string.Empty;

    /// <summary>
    /// Full readable article body (plain text, paragraphs separated by blank
    /// lines), scraped from the source page via readability. Null until the
    /// enrichment pass fills it. Kept out of list responses (heavy); served
    /// only on the single-article detail endpoint.
    /// </summary>
    public string? Content { get; private set; }
    public string? ImageUrl { get; private set; }
    public DateTime PublishedAt { get; private set; }
    public string[] Tags { get; private set; } = Array.Empty<string>();
    public DateTime IngestedAt { get; private set; }

    private NewsArticle() { }

    /// <summary>
    /// Backfill the readable body and/or hero image discovered by the
    /// post-fetch enrichment pass. Only fills fields that are still empty —
    /// never overwrites data the feed already supplied. Returns true when at
    /// least one field changed (so the caller can persist the update).
    /// </summary>
    public bool ApplyEnrichment(string? content, string? imageUrl)
    {
        var changed = false;
        if (string.IsNullOrWhiteSpace(Content) && !string.IsNullOrWhiteSpace(content))
        {
            Content = content.Trim();
            changed = true;
        }
        if (string.IsNullOrWhiteSpace(ImageUrl) && !string.IsNullOrWhiteSpace(imageUrl))
        {
            ImageUrl = imageUrl.Trim();
            changed = true;
        }
        return changed;
    }

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
