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

    /// <summary>
    /// When the readability enrichment pass last tried to scrape this article's
    /// source page (success or failure). Null = never attempted. Used to give
    /// each backlog article exactly one backfill attempt so the limited
    /// per-tick budget rotates through the whole history instead of getting
    /// stuck retrying the same permanently-unreadable pages.
    /// </summary>
    public DateTime? EnrichmentAttemptedAt { get; private set; }

    private NewsArticle() { }

    /// <summary>Stamp that an enrichment attempt was made (regardless of outcome).</summary>
    public void MarkEnrichmentAttempted() => EnrichmentAttemptedAt = DateTime.UtcNow;

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
        IEnumerable<string> tags,
        string? content = null)
    {
        return new NewsArticle
        {
            Id = Guid.NewGuid(),
            Source = source.Trim(),
            SourceUrl = sourceUrl.Trim(),
            Title = title.Trim(),
            Summary = (summary ?? string.Empty).Trim(),
            // Full body from the feed's own content:encoded / atom:content when
            // present — the primary, most reliable source. Scraping only fills
            // this in later if the feed gave nothing.
            Content = string.IsNullOrWhiteSpace(content) ? null : content.Trim(),
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
