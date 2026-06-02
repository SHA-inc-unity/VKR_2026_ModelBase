using Microsoft.EntityFrameworkCore;
using NewsService.Application.Interfaces;
using NewsService.Domain.Entities;
using NewsService.Infrastructure.Data;

namespace NewsService.Infrastructure.Repositories;

public sealed class NewsRepository : INewsRepository
{
    private readonly NewsDbContext _db;

    public NewsRepository(NewsDbContext db) => _db = db;

    public async Task<NewsPage> ListAsync(string? symbol, int page, int pageSize, CancellationToken ct)
    {
        IQueryable<NewsArticle> q = _db.NewsArticles.AsNoTracking();
        if (!string.IsNullOrWhiteSpace(symbol))
        {
            var s = symbol.Trim().ToUpperInvariant();
            // Postgres array contains: tags @> ARRAY[s]
            q = q.Where(x => x.Tags.Contains(s));
        }

        var total = await q.CountAsync(ct);
        var items = await q
            .OrderByDescending(x => x.PublishedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .ToListAsync(ct);

        return new NewsPage { Items = items, Total = total };
    }

    public Task<NewsArticle?> GetByIdAsync(Guid id, CancellationToken ct) =>
        _db.NewsArticles.AsNoTracking().FirstOrDefaultAsync(x => x.Id == id, ct);

    public Task<bool> ExistsByUrlAsync(string sourceUrl, CancellationToken ct) =>
        _db.NewsArticles.AsNoTracking().AnyAsync(x => x.SourceUrl == sourceUrl, ct);

    public async Task<bool> UpsertAsync(NewsArticle article, CancellationToken ct)
    {
        var existing = await _db.NewsArticles
            .FirstOrDefaultAsync(x => x.SourceUrl == article.SourceUrl, ct);

        if (existing is null)
        {
            await _db.NewsArticles.AddAsync(article, ct);
            await _db.SaveChangesAsync(ct);
            return true;
        }

        // No-op update — keep first ingest as canonical row.
        return false;
    }

    public async Task<IReadOnlyList<NewsArticle>> ListNeedingEnrichmentAsync(int limit, CancellationToken ct)
    {
        // Tracked (no AsNoTracking) so ApplyEnrichment + UpdateAsync persist.
        // Only articles never attempted yet → each gets exactly one backfill try.
        return await _db.NewsArticles
            .Where(x => x.EnrichmentAttemptedAt == null && (x.Content == null || x.ImageUrl == null))
            .OrderByDescending(x => x.PublishedAt)
            .Take(limit)
            .ToListAsync(ct);
    }

    public async Task UpdateAsync(NewsArticle article, CancellationToken ct)
    {
        _db.NewsArticles.Update(article);
        await _db.SaveChangesAsync(ct);
    }
}
