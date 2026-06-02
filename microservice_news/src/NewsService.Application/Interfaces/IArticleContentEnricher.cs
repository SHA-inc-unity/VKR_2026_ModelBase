namespace NewsService.Application.Interfaces;

/// <summary>
/// Result of scraping a source article page: the readable body (plain text,
/// paragraphs separated by blank lines) and the best hero image discovered on
/// the page (og:image / readability featured image). Either field may be null
/// when the page is unreadable or blocks scraping.
/// </summary>
public sealed record ArticleEnrichment(string? Content, string? ImageUrl);

/// <summary>
/// Fetches a news article's source page and extracts the full readable body and
/// a hero image, so the feed isn't limited to the short RSS summary and so
/// articles whose feed entry carried no image still get one. Best-effort:
/// implementations must soft-fail (return empty enrichment) rather than throw.
/// </summary>
public interface IArticleContentEnricher
{
    Task<ArticleEnrichment> EnrichAsync(string sourceUrl, CancellationToken ct);
}
