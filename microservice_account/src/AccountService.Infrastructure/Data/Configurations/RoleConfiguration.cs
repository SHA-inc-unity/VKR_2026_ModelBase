using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AccountService.Infrastructure.Data.Configurations;

internal sealed class RoleConfiguration : IEntityTypeConfiguration<Role>
{
    public void Configure(EntityTypeBuilder<Role> builder)
    {
        builder.ToTable("roles");

        builder.HasKey(r => r.Id);
        builder.Property(r => r.Id).ValueGeneratedNever();

        builder.Property(r => r.Code).IsRequired().HasMaxLength(50);
        builder.HasIndex(r => r.Code).IsUnique();

        builder.Property(r => r.Name).IsRequired().HasMaxLength(100);

        // Seed data
        builder.HasData(
            new { Id = 1, Code = Role.Codes.User, Name = "User" },
            new { Id = 2, Code = Role.Codes.Admin, Name = "Administrator" }
        );
    }
}
