using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace NewsService.Infrastructure.Migrations
{
    /// <inheritdoc />
    public partial class AddNewsArticleContent : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "content",
                table: "news_articles",
                type: "text",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "content",
                table: "news_articles");
        }
    }
}
