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
    /// Additional RSS feeds polled on every tick. The defaults are reliable public crypto news
    /// feeds that ship `media:content` / `enclosure` images, which we extract as hero images
    /// for the frontend cards.
    /// </summary>
    public string[] RssFeeds { get; set; } =
    {
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
