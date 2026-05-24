using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using SocialService.Domain.Entities;

namespace SocialService.Infrastructure.Data.Configurations;

public sealed class CommentConfiguration : IEntityTypeConfiguration<Comment>
{
    public void Configure(EntityTypeBuilder<Comment> b)
    {
        b.ToTable("comments");
        b.HasKey(x => x.Id);
        b.Property(x => x.Id).ValueGeneratedNever();
        b.Property(x => x.UserId).IsRequired();
        b.Property(x => x.TargetType).HasMaxLength(16).IsRequired();
        b.Property(x => x.TargetId).HasMaxLength(128).IsRequired();
        b.Property(x => x.ParentId);
        b.Property(x => x.Body).HasMaxLength(4000).IsRequired();
        b.Property(x => x.CreatedAt).IsRequired();
        b.Property(x => x.UpdatedAt).IsRequired();
        b.Property(x => x.DeletedAt);
        b.HasIndex(x => new { x.TargetType, x.TargetId, x.CreatedAt });
        b.HasIndex(x => x.ParentId);
    }
}

public sealed class CommentLikeConfiguration : IEntityTypeConfiguration<CommentLike>
{
    public void Configure(EntityTypeBuilder<CommentLike> b)
    {
        b.ToTable("comment_likes");
        b.HasKey(x => new { x.CommentId, x.UserId });
        b.Property(x => x.CommentId).IsRequired();
        b.Property(x => x.UserId).IsRequired();
        b.Property(x => x.CreatedAt).IsRequired();
        b.HasIndex(x => x.CommentId);
    }
}
