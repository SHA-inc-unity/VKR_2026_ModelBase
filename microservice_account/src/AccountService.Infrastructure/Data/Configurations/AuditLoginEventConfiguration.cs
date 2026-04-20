using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AccountService.Infrastructure.Data.Configurations;

internal sealed class AuditLoginEventConfiguration : IEntityTypeConfiguration<AuditLoginEvent>
{
    public void Configure(EntityTypeBuilder<AuditLoginEvent> builder)
    {
        builder.ToTable("audit_login_events");

        builder.HasKey(e => e.Id);
        builder.Property(e => e.Id).ValueGeneratedNever();

        builder.Property(e => e.EventType)
            .IsRequired()
            .HasConversion<string>()
            .HasMaxLength(50);

        builder.Property(e => e.IpAddress).HasMaxLength(64);
        builder.Property(e => e.UserAgent).HasMaxLength(512);
        builder.Property(e => e.Metadata).HasMaxLength(2048);
        builder.Property(e => e.OccurredAt).IsRequired();
    }
}
