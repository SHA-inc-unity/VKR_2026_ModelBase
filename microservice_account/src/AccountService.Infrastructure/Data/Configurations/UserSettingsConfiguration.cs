using AccountService.Domain.Entities;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace AccountService.Infrastructure.Data.Configurations;

internal sealed class UserSettingsConfiguration : IEntityTypeConfiguration<UserSettings>
{
    public void Configure(EntityTypeBuilder<UserSettings> builder)
    {
        builder.ToTable("user_settings");

        builder.HasKey(s => s.UserId);
        builder.Property(s => s.UserId).ValueGeneratedNever();

        builder.Property(s => s.Theme).IsRequired().HasMaxLength(20).HasDefaultValue("system");
        builder.Property(s => s.Locale).IsRequired().HasMaxLength(10).HasDefaultValue("en");
        builder.Property(s => s.NotificationsEnabled).IsRequired().HasDefaultValue(true);
        builder.Property(s => s.CreatedAt).IsRequired();
        builder.Property(s => s.UpdatedAt).IsRequired();
    }
}
