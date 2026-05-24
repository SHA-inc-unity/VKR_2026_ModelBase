using System.ServiceModel.Syndication;
using System.Text.Json;
using System.Xml;
using Microsoft.Extensions.Options;
using NewsService.Application.Common.Settings;
using NewsService.Application.Interfaces;
using NewsService.Domain.Entities;

namespace NewsService.API.BackgroundJobs;

public sealed class CryptoPanicIngesterService : BackgroundService
{
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
            _log.LogInformation("CryptoPanic ingester disabled by configuration");
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
                _log.LogWarning(ex, "CryptoPanic ingest tick failed");
            }

            try { await Task.Delay(period, stoppingToken); }
            catch (OperationCanceledException) { break; }
        }
    }

    private async Task RunOnceAsync(CancellationToken ct)
    {
        var hasToken = !string.IsNullOrWhiteSpace(_settings.AuthToken);
        var articles = hasToken
            ? await FetchJsonAsync(ct)
            : await FetchRssAsync(ct);

        if (articles.Count == 0)
        {
            _log.LogDebug("CryptoPanic returned no articles");
            return;
        }

        using var scope = _scopes.CreateScope();
        var repo = scope.ServiceProvider.GetRequiredService<INewsRepository>();
        var bus = scope.ServiceProvider.GetRequiredService<INewsEventBus>();

        var created = 0;
        foreach (var article in articles)
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

        _log.LogInformation("CryptoPanic tick: fetched={Fetched} new={Created}", articles.Count, created);
    }

    private async Task<List<NewsArticle>> FetchJsonAsync(CancellationToken ct)
    {
        var url = $"{_settings.PostsUrl}?auth_token={Uri.EscapeDataString(_settings.AuthToken)}&public=true&filter=hot";
        using var client = _http.CreateClient("cryptopanic");
        using var resp = await client.GetAsync(url, ct);
        if (!resp.IsSuccessStatusCode)
        {
            _log.LogWarning("CryptoPanic JSON returned {Status}; falling back to RSS", (int)resp.StatusCode);
            return await FetchRssAsync(ct);
        }

        var json = await resp.Content.ReadAsStringAsync(ct);
        return ParseJson(json);
    }

    private static List<NewsArticle> ParseJson(string json)
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

    private async Task<List<NewsArticle>> FetchRssAsync(CancellationToken ct)
    {
        var result = new List<NewsArticle>();
        using var client = _http.CreateClient("cryptopanic");
        using var resp = await client.GetAsync(_settings.RssUrl, ct);
        if (!resp.IsSuccessStatusCode)
        {
            _log.LogWarning("CryptoPanic RSS returned {Status}", (int)resp.StatusCode);
            return result;
        }

        await using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var reader = XmlReader.Create(stream, new XmlReaderSettings { Async = false, DtdProcessing = DtdProcessing.Ignore });
        SyndicationFeed feed;
        try
        {
            feed = SyndicationFeed.Load(reader);
        }
        catch (Exception ex)
        {
            _log.LogWarning(ex, "Failed to parse RSS feed");
            return result;
        }

        foreach (var item in feed.Items)
        {
            var url = item.Links.FirstOrDefault()?.Uri.ToString();
            if (string.IsNullOrWhiteSpace(url)) continue;
            var title = item.Title?.Text ?? string.Empty;
            if (string.IsNullOrWhiteSpace(title)) continue;
            var summary = item.Summary?.Text ?? string.Empty;
            var publishedAt = item.PublishDate.UtcDateTime;
            if (publishedAt == default) publishedAt = DateTime.UtcNow;

            result.Add(NewsArticle.Create(
                source: feed.Title?.Text ?? "CryptoPanic",
                sourceUrl: url!,
                title: title,
                summary: summary,
                imageUrl: null,
                publishedAt: publishedAt,
                tags: Array.Empty<string>()));
        }

        return result;
    }
}
