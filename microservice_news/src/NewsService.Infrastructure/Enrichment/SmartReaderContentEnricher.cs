using Microsoft.Extensions.Logging;
using NewsService.Application.Interfaces;

namespace NewsService.Infrastructure.Enrichment;

/// <summary>
/// Readability-based article enricher (Mozilla Readability port via SmartReader).
/// Fetches the source page through the shared "cryptopanic" HttpClient (real
/// browser User-Agent + IPv4 + decompression — same anti-throttle setup the RSS
/// fetcher uses) and extracts the main article text and a hero image.
///
/// Strictly best-effort: any failure (network, paywall, unreadable layout) is
/// swallowed and returns an empty enrichment, so a bad scrape never blocks
/// ingestion — the article still lands with its RSS summary.
/// </summary>
public sealed class SmartReaderContentEnricher : IArticleContentEnricher
{
    private const int MaxContentChars = 24_000;

    private readonly IHttpClientFactory _httpFactory;
    private readonly ILogger<SmartReaderContentEnricher> _log;

    public SmartReaderContentEnricher(
        IHttpClientFactory httpFactory,
        ILogger<SmartReaderContentEnricher> log)
    {
        _httpFactory = httpFactory;
        _log = log;
    }

    public async Task<ArticleEnrichment> EnrichAsync(string sourceUrl, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(sourceUrl) ||
            !Uri.TryCreate(sourceUrl, UriKind.Absolute, out _))
        {
            return new ArticleEnrichment(null, null);
        }

        try
        {
            // Fetch the page ourselves through the shared anti-throttle client
            // (UA spoof + IPv4 + decompression) and hand the HTML to SmartReader
            // for parsing only. This avoids depending on SmartReader's own
            // network stack, whose ParseArticleAsync signature shifts between
            // versions (a hard break under TreatWarningsAsErrors).
            var client = _httpFactory.CreateClient("cryptopanic");
            var html = await client.GetStringAsync(sourceUrl, ct);
            if (string.IsNullOrWhiteSpace(html))
            {
                return new ArticleEnrichment(null, null);
            }

            var reader = new SmartReader.Reader(sourceUrl, html);
            var article = reader.GetArticle();

            if (article is null || !article.IsReadable)
            {
                return new ArticleEnrichment(null, NormalizeImage(article?.FeaturedImage));
            }

            var content = NormalizeText(article.TextContent);
            var image = NormalizeImage(article.FeaturedImage);
            return new ArticleEnrichment(content, image);
        }
        catch (OperationCanceledException)
        {
            return new ArticleEnrichment(null, null);
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Article enrichment failed for {Url}", sourceUrl);
            return new ArticleEnrichment(null, null);
        }
    }

    /// <summary>
    /// SmartReader's TextContent keeps block breaks as newlines but is padded
    /// with leading whitespace and blank runs. Collapse to clean paragraphs
    /// (blank-line separated) and cap the length so a runaway page can't bloat
    /// the row.
    /// </summary>
    private static string? NormalizeText(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return null;

        var paragraphs = raw
            .Replace("\r\n", "\n")
            .Split('\n')
            .Select(line => line.Trim())
            .Where(line => line.Length > 0);

        var joined = string.Join("\n\n", paragraphs).Trim();
        if (joined.Length == 0) return null;
        if (joined.Length > MaxContentChars)
        {
            joined = joined.Substring(0, MaxContentChars).TrimEnd() + "…";
        }
        return joined;
    }

    private static string? NormalizeImage(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return null;
        var trimmed = raw.Trim();
        return Uri.TryCreate(trimmed, UriKind.Absolute, out _) ? trimmed : null;
    }
}
