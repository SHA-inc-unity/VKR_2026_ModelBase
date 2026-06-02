using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace NewsService.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddNewsEnrichmentAttempt : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTime>(
                name: "enrichment_attempted_at",
                table: "news_articles",
                type: "timestamp with time zone",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "enrichment_attempted_at",
                table: "news_articles");
        }
    }
}
