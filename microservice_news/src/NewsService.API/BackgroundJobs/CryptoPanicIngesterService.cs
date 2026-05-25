using System.Globalization;
using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Xml.Linq;
using Microsoft.Extensions.Options;
using NewsService.Application.Common.Settings;
using NewsService.Application.Interfaces;
using NewsService.Domain.Entities;

namespace NewsService.API.BackgroundJobs;

/// <summary>
/// Background news ingester. Despite the class name (kept for compatibility with existing
/// DI registration and deployed env vars), the implementation now polls a curated set of
/// public crypto-news RSS feeds and parses each entry's title, summary, publish date, tags
/// and — importantly — a hero image URL (via <c>media:content</c>, <c>media:thumbnail</c>,
/// <c>enclosure</c>, or an inline <c>&lt;img&gt;</c> in the description as a last resort).
///
/// If an optional CryptoPanic auth token is configured, the JSON API is queried as an
/// additional source on each tick.
/// </summary>
public sealed class CryptoPanicIngesterService : BackgroundService
{
    private static readonly XNamespace MediaNs = "http://search.yahoo.com/mrss/";
    private static readonly XNamespace ContentNs = "http://purl.org/rss/1.0/modules/content/";
    private static readonly XNamespace DcNs = "http://purl.org/dc/elements/1.1/";
    private static readonly XNamespace AtomNs = "http://www.w3.org/2005/Atom";

    private static readonly Regex HtmlTagRegex =
        new("<[^>]+>", RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex WhitespaceRegex =
        new("\\s+", RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex InlineImgRegex =
        new("<img[^>]+src=\"([^\"]+)\"", RegexOptions.Compiled | RegexOptions.IgnoreCase);

    private readonly IServiceScopeFactory _scopes;
    private readonly IHttpClientFactory _http;
    private readonly CryptoPanicSettings _settings;
    private readonly ILogger<CryptoPanicIngesterService> _log;

    public CryptoPanicIngesterService(
        IServiceScopeFactory scopes,
        IHttpClientFactory http,
        IOptions<CryptoPanicSettings> opts,
        ILogger<CryptoPanicIngesterService> log)
    {
        _scopes = scopes;
        _http = http;
        _settings = opts.Value;
        _log = log;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_settings.Enabled)
        {
            _log.LogInformation("News ingester disabled by configuration");
            return;
        }

        // Give other dependencies a small head start.
        try { await Task.Delay(TimeSpan.FromSeconds(15), stoppingToken); }
        catch (OperationCanceledException) { return; }

        var period = TimeSpan.FromSeconds(Math.Max(60, _settings.PollIntervalSeconds));

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await RunOnceAsync(stoppingToken);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "News ingest tick failed");
            }

            try { await Task.Delay(period, stoppingToken); }
            catch (OperationCanceledException) { break; }
        }
    }

    private async Task RunOnceAsync(CancellationToken ct)
    {
        var articles = new List<NewsArticle>();

        // 1) Aggregated public RSS feeds (always on — no API key required).
        foreach (var feedUrl in _settings.RssFeeds ?? Array.Empty<string>())
        {
            if (string.IsNullOrWhiteSpace(feedUrl)) continue;
            try
            {
                var fromFeed = await FetchRssAsync(feedUrl, ct);
                _log.LogDebug("RSS {Feed}: parsed {Count} items", feedUrl, fromFeed.Count);
                articles.AddRange(fromFeed);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "RSS feed failed: {Feed}", feedUrl);
            }
        }

        // 2) Optional CryptoPanic JSON (only if an auth token is configured).
        if (!string.IsNullOrWhiteSpace(_settings.AuthToken))
        {
            try
            {
                var cp = await FetchCryptoPanicJsonAsync(ct);
                articles.AddRange(cp);
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "CryptoPanic JSON fetch failed");
            }
        }

        if (articles.Count == 0)
        {
            _log.LogWarning("News ingest produced 0 articles this tick");
            return;
        }

        // Deduplicate within the batch by source URL — different feeds sometimes link the same story.
        var unique = articles
            .GroupBy(a => a.SourceUrl, StringComparer.OrdinalIgnoreCase)
            .Select(g => g.First())
            .ToList();

        using var scope = _scopes.CreateScope();
        var repo = scope.ServiceProvider.GetRequiredService<INewsRepository>();
        var bus = scope.ServiceProvider.GetRequiredService<INewsEventBus>();

        var created = 0;
        foreach (var article in unique)
        {
            try
            {
                var inserted = await repo.UpsertAsync(article, ct);
                if (inserted)
                {
                    created++;
                    await bus.PublishCreatedAsync(article, ct);
                }
            }
            catch (Exception ex)
            {
                _log.LogWarning(ex, "Failed to upsert news {Url}", article.SourceUrl);
            }
        }

        _log.LogInformation("News tick: fetched={Fetched} unique={Unique} new={Created}",
            articles.Count, unique.Count, created);
    }

    // ── RSS parsing ────────────────────────────────────────────────────────

    private async Task<List<NewsArticle>> FetchRssAsync(string feedUrl, CancellationToken ct)
    {
        var result = new List<NewsArticle>();
        var client = _http.CreateClient("cryptopanic");
        using var resp = await client.GetAsync(feedUrl, ct);
        if (!resp.IsSuccessStatusCode)
        {
            _log.LogWarning("RSS {Feed} returned {Status}", feedUrl, (int)resp.StatusCode);
            return result;
        }

        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        XDocument doc;
        try
        {
            doc = await XDocument.LoadAsync(stream, LoadOptions.None, ct);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to parse RSS XML for {Feed}", feedUrl);
            return result;
        }

        var root = doc.Root;
        if (root is null) return result;

        // Resolve channel display name (used as the "source" label on cards).
        var channel = root.Element("channel");
        var defaultSource =
            channel?.Element("title")?.Value?.Trim()
            ?? TryGetHost(feedUrl)
            ?? "News";

        // RSS 2.0 — <rss><channel><item>...</item></channel></rss>
        var items = channel?.Elements("item") ?? Enumerable.Empty<XElement>();
        foreach (var item in items)
        {
            try
            {
                var article = ParseRssItem(item, defaultSource);
                if (article is not null) result.Add(article);
            }
            catch (Exception ex)
            {
                _log.LogDebug(ex, "Skipping malformed RSS item from {Feed}", feedUrl);
            }
        }

        // Atom fallback — <feed><entry>...</entry></feed>
        if (result.Count == 0 && root.Name.LocalName == "feed")
        {
            foreach (var entry in root.Elements(AtomNs + "entry"))
            {
                try
                {
                    var article = ParseAtomEntry(entry, defaultSource);
                    if (article is not null) result.Add(article);
                }
                catch (Exception ex)
                {
                    _log.LogDebug(ex, "Skipping malformed Atom entry from {Feed}", feedUrl);
                }
            }
        }

        return result;
    }

    private static NewsArticle? ParseRssItem(XElement item, string defaultSource)
    {
        var title = NormalizeText(item.Element("title")?.Value);
        var link = item.Element("link")?.Value?.Trim();
        if (string.IsNullOrWhiteSpace(link))
        {
            // Some feeds put the link inside an atom:link element.
            link = item.Element(AtomNs + "link")?.Attribute("href")?.Value?.Trim();
        }
        if (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(link)) return null;

        var descriptionRaw =
            item.Element("description")?.Value
            ?? item.Element(ContentNs + "encoded")?.Value
            ?? string.Empty;
        var summary = StripHtml(descriptionRaw);

        var publishedAt = ParseDate(item.Element("pubDate")?.Value)
                          ?? ParseDate(item.Element(DcNs + "date")?.Value)
                          ?? DateTime.UtcNow;

        var source = item.Element(DcNs + "creator")?.Value?.Trim() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(source) || source.Length > 60)
        {
            source = defaultSource;
        }

        var imageUrl = ExtractImageUrl(item, descriptionRaw);
        var tags = ExtractTags(item);

        return NewsArticle.Create(
            source: source,
            sourceUrl: link!,
            title: title,
            summary: Truncate(summary, 2000),
            imageUrl: imageUrl,
            publishedAt: publishedAt,
            tags: tags);
    }

    private static NewsArticle? ParseAtomEntry(XElement entry, string defaultSource)
    {
        var title = NormalizeText(entry.Element(AtomNs + "title")?.Value);
        var link = entry.Elements(AtomNs + "link")
            .Select(l => l.Attribute("href")?.Value)
            .FirstOrDefault(v => !string.IsNullOrWhiteSpace(v));
        if (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(link)) return null;

        var summaryRaw =
            entry.Element(AtomNs + "summary")?.Value
            ?? entry.Element(AtomNs + "content")?.Value
            ?? string.Empty;
        var summary = StripHtml(summaryRaw);

        var publishedAt = ParseDate(entry.Element(AtomNs + "published")?.Value)
                          ?? ParseDate(entry.Element(AtomNs + "updated")?.Value)
                          ?? DateTime.UtcNow;

        var imageUrl = ExtractImageUrl(entry, summaryRaw);

        var tags = entry.Elements(AtomNs + "category")
            .Select(c => c.Attribute("term")?.Value)
            .Where(v => !string.IsNullOrWhiteSpace(v))
            .Cast<string>()
            .ToList();

        return NewsArticle.Create(
            source: defaultSource,
            sourceUrl: link!.Trim(),
            title: title,
            summary: Truncate(summary, 2000),
            imageUrl: imageUrl,
            publishedAt: publishedAt,
            tags: tags);
    }

    private static string? ExtractImageUrl(XElement item, string? descriptionHtml)
    {
        // 1) <media:content url="..." medium="image">
        foreach (var media in item.Elements(MediaNs + "content"))
        {
            var url = media.Attribute("url")?.Value;
            var medium = media.Attribute("medium")?.Value;
            var type = media.Attribute("type")?.Value;
            if (string.IsNullOrWhiteSpace(url)) continue;
            if (medium is "video") continue;
            if (!string.IsNullOrWhiteSpace(type) && type.StartsWith("video", StringComparison.OrdinalIgnoreCase)) continue;
            if (LooksLikeImage(url, type)) return url.Trim();
        }

        // 2) <media:thumbnail url="...">
        foreach (var thumb in item.Elements(MediaNs + "thumbnail"))
        {
            var url = thumb.Attribute("url")?.Value;
            if (!string.IsNullOrWhiteSpace(url)) return url.Trim();
        }

        // 3) <enclosure url="..." type="image/*">
        foreach (var enc in item.Elements("enclosure"))
        {
            var url = enc.Attribute("url")?.Value;
            var type = enc.Attribute("type")?.Value;
            if (string.IsNullOrWhiteSpace(url)) continue;
            if (LooksLikeImage(url, type)) return url.Trim();
        }

        // 4) First <img src="..."> in description / content:encoded.
        if (!string.IsNullOrEmpty(descriptionHtml))
        {
            var match = InlineImgRegex.Match(descriptionHtml);
            if (match.Success && match.Groups.Count > 1)
            {
                var url = WebUtility.HtmlDecode(match.Groups[1].Value).Trim();
                if (!string.IsNullOrWhiteSpace(url)) return url;
            }
        }

        return null;
    }

    private static bool LooksLikeImage(string url, string? mimeType)
    {
        if (!string.IsNullOrWhiteSpace(mimeType) &&
            mimeType.StartsWith("image", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        var lower = url.ToLowerInvariant();
        return lower.Contains(".jpg") || lower.Contains(".jpeg") || lower.Contains(".png") ||
               lower.Contains(".webp") || lower.Contains(".gif") || lower.Contains("/image");
    }

    private static List<string> ExtractTags(XElement item)
    {
        var tags = new List<string>();
        foreach (var category in item.Elements("category"))
        {
            var value = category.Value?.Trim();
            if (!string.IsNullOrWhiteSpace(value) && value.Length <= 48) tags.Add(value);
        }
        return tags;
    }

    private static DateTime? ParseDate(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return null;
        if (DateTime.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var dt))
        {
            return dt.ToUniversalTime();
        }
        if (DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var dto))
        {
            return dto.UtcDateTime;
        }
        return null;
    }

    private static string NormalizeText(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return string.Empty;
        var decoded = WebUtility.HtmlDecode(raw);
        return WhitespaceRegex.Replace(decoded, " ").Trim();
    }

    private static string StripHtml(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return string.Empty;
        var withoutTags = HtmlTagRegex.Replace(raw, " ");
        return NormalizeText(withoutTags);
    }

    private static string Truncate(string value, int maxLength)
    {
        if (value.Length <= maxLength) return value;
        return value.Substring(0, maxLength).TrimEnd() + "…";
    }

    private static string? TryGetHost(string url)
    {
        return Uri.TryCreate(url, UriKind.Absolute, out var uri) ? uri.Host : null;
    }

    // ── CryptoPanic JSON (optional, only when AuthToken is set) ────────────

    private async Task<List<NewsArticle>> FetchCryptoPanicJsonAsync(CancellationToken ct)
    {
        var url = $"{_settings.PostsUrl}?auth_token={Uri.EscapeDataString(_settings.AuthToken)}&public=true&filter=hot";
        var client = _http.CreateClient("cryptopanic");
        using var resp = await client.GetAsync(url, ct);
        if (!resp.IsSuccessStatusCode)
        {
            _log.LogWarning("CryptoPanic JSON returned {Status}", (int)resp.StatusCode);
            return new List<NewsArticle>();
        }

        var json = await resp.Content.ReadAsStringAsync(ct);
        return ParseCryptoPanicJson(json);
    }

    private static List<NewsArticle> ParseCryptoPanicJson(string json)
    {
        var result = new List<NewsArticle>();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("results", out var results) || results.ValueKind != JsonValueKind.Array)
            return result;

        foreach (var item in results.EnumerateArray())
        {
            try
            {
                var title = item.TryGetProperty("title", out var t) ? t.GetString() ?? string.Empty : string.Empty;
                var url = item.TryGetProperty("url", out var u) ? u.GetString() ?? string.Empty : string.Empty;
                if (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(url)) continue;

                var publishedAt = DateTime.UtcNow;
                if (item.TryGetProperty("published_at", out var p) && p.ValueKind == JsonValueKind.String &&
                    DateTime.TryParse(p.GetString(), out var pdt))
                {
                    publishedAt = pdt.ToUniversalTime();
                }

                var source = "CryptoPanic";
                if (item.TryGetProperty("source", out var src) && src.ValueKind == JsonValueKind.Object &&
                    src.TryGetProperty("title", out var srcTitle))
                {
                    source = srcTitle.GetString() ?? source;
                }

                var summary = string.Empty;
                if (item.TryGetProperty("description", out var d) && d.ValueKind == JsonValueKind.String)
                {
                    summary = d.GetString() ?? string.Empty;
                }

                var tags = new List<string>();
                if (item.TryGetProperty("currencies", out var currencies) && currencies.ValueKind == JsonValueKind.Array)
                {
                    foreach (var c in currencies.EnumerateArray())
                    {
                        if (c.TryGetProperty("code", out var code) && code.ValueKind == JsonValueKind.String)
                        {
                            var code2 = code.GetString();
                            if (!string.IsNullOrWhiteSpace(code2)) tags.Add(code2!);
                        }
                    }
                }

                result.Add(NewsArticle.Create(
                    source: source,
                    sourceUrl: url,
                    title: title,
                    summary: summary,
                    imageUrl: null,
                    publishedAt: publishedAt,
                    tags: tags));
            }
            catch
            {
                // Skip malformed item.
            }
        }

        return result;
    }
}
