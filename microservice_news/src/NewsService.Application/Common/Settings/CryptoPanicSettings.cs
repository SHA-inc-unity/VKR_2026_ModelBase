namespace NewsService.Application.Common.Settings;

/// <summary>
/// Configuration for the news ingester.
///
/// Historically only CryptoPanic was supported (hence the legacy class/section name kept for
/// backward compatibility with deployed env vars), but the ingester now aggregates a fixed list
/// of public RSS feeds — Cointelegraph, CoinDesk, Decrypt, Bitcoin Magazine — plus an optional
/// CryptoPanic fallback when an auth token is supplied.
/// </summary>
public sealed class CryptoPanicSettings
{
    public const string SectionName = "News:CryptoPanic";

    public string AuthToken { get; set; } = string.Empty;
    public int PollIntervalSeconds { get; set; } = 300; // 5 min
    public string PostsUrl { get; set; } = "https://cryptopanic.com/api/v1/posts/";
    public string RssUrl { get; set; } = "https://cryptopanic.com/news/rss/";
    public bool Enabled { get; set; } = true;

    /// <summary>
    /// RSS feeds polled on every tick. Two groups:
    /// * Full-text feeds (dailyhodl, coinjournal) ship the whole article in
    ///   `content:encoded` — the reliable primary source for the detail body
    ///   (no scraping needed); coinjournal also carries media images and
    ///   dailyhodl embeds an inline `&lt;img&gt;` we extract as the hero.
    /// * Image-rich headline feeds (Cointelegraph, CoinDesk, Decrypt, Bitcoin
    ///   Magazine) ship `media:content`/`enclosure` hero images but only a short
    ///   description — their full body is filled in (best-effort) by the
    ///   readability scrape fallback.
    /// </summary>
    public string[] RssFeeds { get; set; } =
    {
        // Full article body via content:encoded.
        "https://dailyhodl.com/feed/",
        "https://coinjournal.net/feed/",
        // Headline feeds with hero images (body via scrape fallback).
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://decrypt.co/feed",
        "https://bitcoinmagazine.com/.rss/full/",
    };
}

public sealed class NewsKafkaSettings
{
    public const string SectionName = "Kafka";

    public string BootstrapServers { get; set; } = "redpanda:29092";
    public string NewsEventsTopic { get; set; } = "events.news.v1";
}
