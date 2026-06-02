namespace NewsService.Application.DTOs;

public sealed class NewsArticleResponse
{
    public Guid Id { get; set; }
    public string Source { get; set; } = string.Empty;
    public string SourceUrl { get; set; } = string.Empty;
    public string Title { get; set; } = string.Empty;
    public string Summary { get; set; } = string.Empty;

    /// <summary>
    /// Full readable article body (plain text, blank-line separated paragraphs).
    /// Populated only on the single-article detail response; null in list rows
    /// to keep the feed payload small.
    /// </summary>
    public string? Content { get; set; }
    public string? ImageUrl { get; set; }
    public DateTime PublishedAt { get; set; }
    public IReadOnlyList<string> Tags { get; set; } = Array.Empty<string>();
}

public sealed class NewsListResponse
{
    public IReadOnlyList<NewsArticleResponse> Items { get; set; } = Array.Empty<NewsArticleResponse>();
    public int Total { get; set; }
    public int Page { get; set; }
    public int PageSize { get; set; }
}
