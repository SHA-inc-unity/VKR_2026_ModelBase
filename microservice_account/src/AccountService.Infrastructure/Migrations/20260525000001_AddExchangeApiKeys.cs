using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace AccountService.Infrastructure.Migrations;

/// <inheritdoc />
public partial class AddExchangeApiKeys : Migration
{
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.CreateTable(
            name: "exchange_api_keys",
            columns: table => new
            {
                id = table.Column<Guid>(type: "uuid", nullable: false),
                user_id = table.Column<Guid>(type: "uuid", nullable: false),
                exchange = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                label = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false),
                api_key_enc = table.Column<string>(type: "character varying(1024)", maxLength: 1024, nullable: false),
                api_secret_enc = table.Column<string>(type: "character varying(2048)", maxLength: 2048, nullable: false),
                api_key_masked = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false),
                can_read = table.Column<bool>(type: "boolean", nullable: false),
                can_trade = table.Column<bool>(type: "boolean", nullable: false),
                created_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
                last_used_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
                status = table.Column<string>(type: "character varying(20)", maxLength: 20, nullable: false),
                last_validation_error = table.Column<string>(type: "character varying(512)", maxLength: 512, nullable: true),
                last_validated_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: true),
            },
            constraints: table =>
            {
                table.PrimaryKey("pk_exchange_api_keys", x => x.id);
            });

        migrationBuilder.CreateIndex(
            name: "ix_exchange_api_keys_user_id_exchange",
            table: "exchange_api_keys",
            columns: ["user_id", "exchange"]);

        migrationBuilder.CreateTable(
            name: "exchange_metadata",
            columns: table => new
            {
                id = table.Column<Guid>(type: "uuid", nullable: false),
                exchange = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                symbol = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                category = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                maker_fee_bps = table.Column<decimal>(type: "numeric(18,6)", nullable: true),
                taker_fee_bps = table.Column<decimal>(type: "numeric(18,6)", nullable: true),
                min_notional = table.Column<decimal>(type: "numeric(28,8)", nullable: true),
                max_leverage = table.Column<decimal>(type: "numeric(8,2)", nullable: true),
                raw_json = table.Column<string>(type: "jsonb", nullable: false),
                captured_at = table.Column<DateTime>(type: "timestamp with time zone", nullable: false),
            },
            constraints: table =>
            {
                table.PrimaryKey("pk_exchange_metadata", x => x.id);
            });

        migrationBuilder.CreateIndex(
            name: "ix_exchange_metadata_exchange_symbol_category",
            table: "exchange_metadata",
            columns: ["exchange", "symbol", "category"],
            unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.DropTable(name: "exchange_api_keys");
        migrationBuilder.DropTable(name: "exchange_metadata");
    }
}
