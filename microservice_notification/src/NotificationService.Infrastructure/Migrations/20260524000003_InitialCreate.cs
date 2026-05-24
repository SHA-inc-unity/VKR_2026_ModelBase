using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace NotificationService.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class InitialCreate : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "notifications",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    kind = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false),
                    title = table.Column<string>(type: "character varying(256)", maxLength: 256, nullable: false),
                    body = table.Column<string>(type: "character varying(2048)", maxLength: 2048, nullable: false),
                    deeplink = table.Column<string>(type: "character varying(1024)", maxLength: 1024, nullable: true),
                    payload_json = table.Column<string>(type: "jsonb", nullable: true),
                    dedup_key = table.Column<string>(type: "character varying(256)", maxLength: 256, nullable: true),
                    created_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    read_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                },
                constraints: table => table.PrimaryKey("pk_notifications", x => x.id));

            migrationBuilder.CreateTable(
                name: "notification_settings",
                columns: table => new
                {
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    enable_reply = table.Column<bool>(type: "boolean", nullable: false),
                    enable_news = table.Column<bool>(type: "boolean", nullable: false),
                    enable_price = table.Column<bool>(type: "boolean", nullable: false),
                    price_threshold_pct = table.Column<decimal>(type: "numeric(8,4)", nullable: false),
                    updated_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                },
                constraints: table => table.PrimaryKey("pk_notification_settings", x => x.user_id));

            migrationBuilder.CreateIndex(
                name: "ix_notifications_user_id_created_at",
                table: "notifications",
                columns: new[] { "user_id", "created_at" });

            migrationBuilder.CreateIndex(
                name: "ix_notifications_user_id_read_at",
                table: "notifications",
                columns: new[] { "user_id", "read_at" });

            migrationBuilder.CreateIndex(
                name: "ix_notifications_dedup",
                table: "notifications",
                columns: new[] { "user_id", "kind", "dedup_key" });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(name: "notifications");
            migrationBuilder.DropTable(name: "notification_settings");
        }
    }
}
