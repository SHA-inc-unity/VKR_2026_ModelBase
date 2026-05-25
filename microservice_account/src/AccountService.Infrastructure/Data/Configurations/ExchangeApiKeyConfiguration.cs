using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AccountService.Infrastructure.Data.Configurations;

internal sealed class ExchangeApiKeyConfiguration : IEntityTypeConfiguration<ExchangeApiKey>
{
    public void Configure(EntityTypeBuilder<ExchangeApiKey> builder)
    {
        builder.ToTable("exchange_api_keys");
        builder.HasKey(k => k.Id);
        builder.Property(k => k.Id).ValueGeneratedNever();

        builder.Property(k => k.UserId).IsRequired();
        builder.Property(k => k.Exchange).IsRequired().HasMaxLength(32);
        builder.Property(k => k.Label).IsRequired().HasMaxLength(64);

        builder.Property(k => k.ApiKeyEnc).IsRequired().HasMaxLength(1024);
        builder.Property(k => k.ApiSecretEnc).IsRequired().HasMaxLength(2048);
        builder.Property(k => k.ApiKeyMasked).IsRequired().HasMaxLength(64);

        builder.Property(k => k.CanRead).IsRequired();
        builder.Property(k => k.CanTrade).IsRequired();
        builder.Property(k => k.Status).IsRequired().HasMaxLength(20);
        builder.Property(k => k.CreatedAt).IsRequired();
        builder.Property(k => k.LastValidationError).HasMaxLength(512);

        builder.HasIndex(k => new { k.UserId, k.Exchange });
    }
}

internal sealed class ExchangeMetadataConfiguration : IEntityTypeConfiguration<ExchangeMetadata>
{
    public void Configure(EntityTypeBuilder<ExchangeMetadata> builder)
    {
        builder.ToTable("exchange_metadata");
        builder.HasKey(m => m.Id);
        builder.Property(m => m.Id).ValueGeneratedNever();

        builder.Property(m => m.Exchange).IsRequired().HasMaxLength(32);
        builder.Property(m => m.Symbol).IsRequired().HasMaxLength(32);
        builder.Property(m => m.Category).IsRequired().HasMaxLength(32);

        builder.Property(m => m.MakerFeeBps).HasColumnType("numeric(18,6)");
        builder.Property(m => m.TakerFeeBps).HasColumnType("numeric(18,6)");
        builder.Property(m => m.MinNotional).HasColumnType("numeric(28,8)");
        builder.Property(m => m.MaxLeverage).HasColumnType("numeric(8,2)");

        builder.Property(m => m.RawJson).HasColumnType("jsonb");
        builder.Property(m => m.CapturedAt).IsRequired();

        builder.HasIndex(m => new { m.Exchange, m.Symbol, m.Category }).IsUnique();
    }
}
