using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using NewsService.Domain.Entities;

namespace NewsService.Infrastructure.Data.Configurations;

public sealed class NewsArticleConfiguration : IEntityTypeConfiguration<NewsArticle>
{
    public void Configure(EntityTypeBuilder<NewsArticle> b)
    {
        b.ToTable("news_articles");
        b.HasKey(x => x.Id).HasName("pk_news_articles");

        b.Property(x => x.Id).HasColumnName("id");
        b.Property(x => x.Source).HasColumnName("source").HasMaxLength(64).IsRequired();
        b.Property(x => x.SourceUrl).HasColumnName("source_url").HasMaxLength(1024).IsRequired();
        b.Property(x => x.Title).HasColumnName("title").HasMaxLength(512).IsRequired();
        b.Property(x => x.Summary).HasColumnName("summary").HasColumnType("text").IsRequired();
        b.Property(x => x.ImageUrl).HasColumnName("image_url").HasMaxLength(1024);
        b.Property(x => x.Content).HasColumnName("content").HasColumnType("text");
        b.Property(x => x.PublishedAt).HasColumnName("published_at");
        b.Property(x => x.EnrichmentAttemptedAt).HasColumnName("enrichment_attempted_at");
        b.Property(x => x.Tags).HasColumnName("tags").HasColumnType("text[]").IsRequired();
        b.Property(x => x.IngestedAt).HasColumnName("ingested_at");

        b.HasIndex(x => x.SourceUrl).IsUnique().HasDatabaseName("ux_news_articles_source_url");
        b.HasIndex(x => x.PublishedAt).HasDatabaseName("ix_news_articles_published_at");
        b.HasIndex(x => x.Tags).HasMethod("gin").HasDatabaseName("ix_news_articles_tags_gin");
    }
}
