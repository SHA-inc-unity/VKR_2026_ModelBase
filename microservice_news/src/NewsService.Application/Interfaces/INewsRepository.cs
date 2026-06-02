using NewsService.Domain.Entities;

namespace NewsService.Application.Interfaces;

public sealed class NewsPage
{
    public IReadOnlyList<NewsArticle> Items { get; init; } = Array.Empty<NewsArticle>();
    public int Total { get; init; }
}

public interface INewsRepository
{
    Task<NewsPage> ListAsync(string? symbol, int page, int pageSize, CancellationToken ct);
    Task<NewsArticle?> GetByIdAsync(Guid id, CancellationToken ct);
    Task<bool> ExistsByUrlAsync(string sourceUrl, CancellationToken ct);
    /// <summary>Upsert by source_url. Returns true iff a NEW row was inserted (so callers can fire news.created).</summary>
    Task<bool> UpsertAsync(NewsArticle article, CancellationToken ct);

    /// <summary>
    /// Backlog articles still missing a body or image that have never had an
    /// enrichment attempt — newest first, capped at <paramref name="limit"/>.
    /// Returned tracked so the caller can mutate + persist via <see cref="UpdateAsync"/>.
    /// </summary>
    Task<IReadOnlyList<NewsArticle>> ListNeedingEnrichmentAsync(int limit, CancellationToken ct);

    /// <summary>Persist mutations to an already-loaded article (enrichment backfill).</summary>
    Task UpdateAsync(NewsArticle article, CancellationToken ct);
}

public interface INewsEventBus
{
    Task PublishCreatedAsync(NewsArticle article, CancellationToken ct);
}
