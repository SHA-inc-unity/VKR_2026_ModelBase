using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace AccountService.Infrastructure.Migrations;

/// <inheritdoc />
public partial class AddGuestRole : Migration
{
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.InsertData(
            table: "roles",
            columns: ["id", "code", "name"],
            values: new object[] { 0, "guest", "Guest" });
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.DeleteData(
            table: "roles",
            keyColumn: "id",
            keyValue: 0);
    }
}