using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AccountService.Infrastructure.Data.Configurations;

internal sealed class RefreshTokenConfiguration : IEntityTypeConfiguration<RefreshToken>
{
    public void Configure(EntityTypeBuilder<RefreshToken> builder)
    {
        builder.ToTable("refresh_tokens");

        builder.HasKey(t => t.Id);
        builder.Property(t => t.Id).ValueGeneratedNever();

        builder.Property(t => t.TokenHash).IsRequired().HasMaxLength(512);
        builder.HasIndex(t => t.TokenHash).IsUnique();

        builder.Property(t => t.DeviceId).HasMaxLength(128);
        builder.Property(t => t.IpAddress).HasMaxLength(64);
        builder.Property(t => t.UserAgent).HasMaxLength(512);

        builder.Property(t => t.ExpiresAt).IsRequired();
        builder.Property(t => t.CreatedAt).IsRequired();
    }
}
