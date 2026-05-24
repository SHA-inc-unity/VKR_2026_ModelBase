namespace NewsService.Application.Common.Settings;

public sealed class CryptoPanicSettings
{
    public const string SectionName = "News:CryptoPanic";

    public string AuthToken { get; set; } = string.Empty;
    public int PollIntervalSeconds { get; set; } = 300; // 5 min
    public string PostsUrl { get; set; } = "https://cryptopanic.com/api/v1/posts/";
    public string RssUrl { get; set; } = "https://cryptopanic.com/news/rss/";
    public bool Enabled { get; set; } = true;
}

public sealed class NewsKafkaSettings
{
    public const string SectionName = "Kafka";

    public string BootstrapServers { get; set; } = "redpanda:29092";
    public string NewsEventsTopic { get; set; } = "events.news.v1";
}
