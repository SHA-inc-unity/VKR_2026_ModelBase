using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using SocialService.Domain.Entities;

namespace SocialService.Infrastructure.Data.Configurations;

public sealed class FavoriteConfiguration : IEntityTypeConfiguration<Favorite>
{
    public void Configure(EntityTypeBuilder<Favorite> b)
    {
        b.ToTable("favorites");
        b.HasKey(x => new { x.UserId, x.Symbol });
        b.Property(x => x.UserId).IsRequired();
        b.Property(x => x.Symbol).HasMaxLength(32).IsRequired();
        b.Property(x => x.CreatedAt).IsRequired();
        b.HasIndex(x => x.Symbol);
    }
}
