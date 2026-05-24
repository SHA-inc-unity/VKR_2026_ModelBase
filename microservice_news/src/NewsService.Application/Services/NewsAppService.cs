using NewsService.Application.DTOs;
using NewsService.Application.Interfaces;
using NewsService.Domain.Entities;

namespace NewsService.Application.Services;

public interface INewsAppService
{
    Task<NewsListResponse> ListAsync(string? symbol, int page, int pageSize, CancellationToken ct);
    Task<NewsArticleResponse?> GetAsync(Guid id, CancellationToken ct);
}

public sealed class NewsAppService : INewsAppService
{
    private readonly INewsRepository _repo;
    public NewsAppService(INewsRepository repo) => _repo = repo;

    public async Task<NewsListResponse> ListAsync(string? symbol, int page, int pageSize, CancellationToken ct)
    {
        if (page < 1) page = 1;
        if (pageSize < 1) pageSize = 30;
        if (pageSize > 100) pageSize = 100;
        symbol = string.IsNullOrWhiteSpace(symbol) ? null : symbol.Trim().ToUpperInvariant();
        var slice = await _repo.ListAsync(symbol, page, pageSize, ct);
        return new NewsListResponse
        {
            Items = slice.Items.Select(Map).ToList(),
            Total = slice.Total,
            Page = page,
            PageSize = pageSize,
        };
    }

    public async Task<NewsArticleResponse?> GetAsync(Guid id, CancellationToken ct)
    {
        var article = await _repo.GetByIdAsync(id, ct);
        return article is null ? null : Map(article);
    }

    private static NewsArticleResponse Map(NewsArticle a) => new()
    {
        Id = a.Id,
        Source = a.Source,
        SourceUrl = a.SourceUrl,
        Title = a.Title,
        Summary = a.Summary,
        ImageUrl = a.ImageUrl,
        PublishedAt = a.PublishedAt,
        Tags = a.Tags,
    };
}
