using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace NotificationService.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddPriceAlerts : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "price_alerts",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    symbol = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                    condition = table.Column<string>(type: "character varying(8)", maxLength: 8, nullable: false),
                    target_price = table.Column<decimal>(type: "numeric", nullable: false),
                    is_enabled = table.Column<bool>(type: "boolean", nullable: false),
                    is_armed = table.Column<bool>(type: "boolean", nullable: false),
                    last_triggered_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                    last_observed_price = table.Column<decimal>(type: "numeric", nullable: true),
                    created_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                    updated_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                },
                constraints: table => table.PrimaryKey("pk_price_alerts", x => x.id));

            migrationBuilder.CreateIndex(
                name: "ix_price_alerts_user_id",
                table: "price_alerts",
                column: "user_id");

            migrationBuilder.CreateIndex(
                name: "ix_price_alerts_is_enabled",
                table: "price_alerts",
                column: "is_enabled");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(name: "price_alerts");
        }
    }
}
