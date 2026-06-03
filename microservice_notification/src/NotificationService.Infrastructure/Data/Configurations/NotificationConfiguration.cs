using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using NotificationService.Domain.Entities;

namespace NotificationService.Infrastructure.Data.Configurations;

public sealed class NotificationConfiguration : IEntityTypeConfiguration<Notification>
{
    public void Configure(EntityTypeBuilder<Notification> b)
    {
        b.ToTable("notifications");
        b.HasKey(x => x.Id).HasName("pk_notifications");

        b.Property(x => x.Id).HasColumnName("id");
        b.Property(x => x.UserId).HasColumnName("user_id");
        b.Property(x => x.Kind).HasColumnName("kind").HasMaxLength(64).IsRequired();
        b.Property(x => x.Title).HasColumnName("title").HasMaxLength(256).IsRequired();
        b.Property(x => x.Body).HasColumnName("body").HasMaxLength(2048).IsRequired();
        b.Property(x => x.Deeplink).HasColumnName("deeplink").HasMaxLength(1024);
        b.Property(x => x.PayloadJson).HasColumnName("payload_json").HasColumnType("jsonb");
        b.Property(x => x.DedupKey).HasColumnName("dedup_key").HasMaxLength(256);
        b.Property(x => x.CreatedAt).HasColumnName("created_at");
        b.Property(x => x.ReadAt).HasColumnName("read_at");

        b.HasIndex(x => new { x.UserId, x.CreatedAt }).HasDatabaseName("ix_notifications_user_id_created_at");
        b.HasIndex(x => new { x.UserId, x.ReadAt }).HasDatabaseName("ix_notifications_user_id_read_at");
        b.HasIndex(x => new { x.UserId, x.Kind, x.DedupKey }).HasDatabaseName("ix_notifications_dedup");
    }
}

public sealed class NotificationSettingsConfiguration : IEntityTypeConfiguration<NotificationSettings>
{
    public void Configure(EntityTypeBuilder<NotificationSettings> b)
    {
        b.ToTable("notification_settings");
        b.HasKey(x => x.UserId).HasName("pk_notification_settings");

        b.Property(x => x.UserId).HasColumnName("user_id");
        b.Property(x => x.EnableReply).HasColumnName("enable_reply");
        b.Property(x => x.EnableNews).HasColumnName("enable_news");
        b.Property(x => x.EnablePrice).HasColumnName("enable_price");
        b.Property(x => x.PriceThresholdPct).HasColumnName("price_threshold_pct").HasColumnType("numeric(8,4)");
        b.Property(x => x.UpdatedAt).HasColumnName("updated_at");
    }
}

public sealed class PushSubscriptionConfiguration : IEntityTypeConfiguration<PushSubscription>
{
    public void Configure(EntityTypeBuilder<PushSubscription> b)
    {
        b.ToTable("push_subscriptions");
        b.HasKey(x => x.Id).HasName("pk_push_subscriptions");

        b.Property(x => x.Id).HasColumnName("id");
        b.Property(x => x.UserId).HasColumnName("user_id");
        b.Property(x => x.Endpoint).HasColumnName("endpoint").HasMaxLength(2048).IsRequired();
        b.Property(x => x.P256dh).HasColumnName("p256dh").HasMaxLength(256).IsRequired();
        b.Property(x => x.Auth).HasColumnName("auth").HasMaxLength(256).IsRequired();
        b.Property(x => x.UserAgent).HasColumnName("user_agent").HasMaxLength(512);
        b.Property(x => x.CreatedAt).HasColumnName("created_at");
        b.Property(x => x.LastSeenAt).HasColumnName("last_seen_at");
        b.Property(x => x.FailureCount).HasColumnName("failure_count");

        b.HasIndex(x => x.Endpoint).IsUnique().HasDatabaseName("ux_push_subscriptions_endpoint");
        b.HasIndex(x => x.UserId).HasDatabaseName("ix_push_subscriptions_user_id");
    }
}

public sealed class PriceAlertConfiguration : IEntityTypeConfiguration<PriceAlert>
{
    public void Configure(EntityTypeBuilder<PriceAlert> b)
    {
        b.ToTable("price_alerts");
        b.HasKey(x => x.Id).HasName("pk_price_alerts");

        b.Property(x => x.Id).HasColumnName("id");
        b.Property(x => x.UserId).HasColumnName("user_id");
        b.Property(x => x.Symbol).HasColumnName("symbol").HasMaxLength(32).IsRequired();
        b.Property(x => x.Condition).HasColumnName("condition").HasMaxLength(8).IsRequired();
        b.Property(x => x.TargetPrice).HasColumnName("target_price").HasColumnType("numeric");
        b.Property(x => x.IsEnabled).HasColumnName("is_enabled");
        b.Property(x => x.IsArmed).HasColumnName("is_armed");
        b.Property(x => x.LastTriggeredAt).HasColumnName("last_triggered_at");
        b.Property(x => x.LastObservedPrice).HasColumnName("last_observed_price").HasColumnType("numeric");
        b.Property(x => x.CreatedAt).HasColumnName("created_at");
        b.Property(x => x.UpdatedAt).HasColumnName("updated_at");

        b.HasIndex(x => x.UserId).HasDatabaseName("ix_price_alerts_user_id");
        b.HasIndex(x => x.IsEnabled).HasDatabaseName("ix_price_alerts_is_enabled");
    }
}
