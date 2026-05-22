using Dapper;

namespace DataService.API.Database;

public sealed record MarketWatchCandleSnapshot(
    long BucketStartMs,
    decimal Open,
    decimal High,
    decimal Low,
    decimal Close,
    long LastUpdateMs);

public sealed record MarketWatchSymbolSnapshot(
    string Exchange,
    string Symbol,
    decimal LastPrice,
    DateTimeOffset LastPriceTimestampUtc,
    string CandlesJson);

public sealed class MarketWatchRepository
{
    private const string CreateSchemaSql = """
        CREATE TABLE IF NOT EXISTS market_watch_live (
            exchange       text        NOT NULL,
            symbol         text        NOT NULL,
            last_price     numeric     NOT NULL,
            last_price_ts  timestamptz NOT NULL,
            candles_json   jsonb       NOT NULL DEFAULT '{}'::jsonb,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (exchange, symbol)
        );

        CREATE INDEX IF NOT EXISTS ix_market_watch_live_updated_at
            ON market_watch_live (updated_at DESC);
        """;

    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<MarketWatchRepository> _log;
    private volatile bool _schemaReady;

    public MarketWatchRepository(PostgresConnectionFactory pg, ILogger<MarketWatchRepository> log)
    {
        _pg = pg;
        _log = log;
    }

    public bool SchemaReady => _schemaReady;

    public async Task EnsureSchemaAsync(CancellationToken ct = default)
    {
        if (_schemaReady) return;

        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(CreateSchemaSql, cancellationToken: ct));
        _schemaReady = true;
        _log.LogInformation("market_watch_live schema ensured");
    }

    public async Task UpsertSnapshotsAsync(
        IReadOnlyCollection<MarketWatchSymbolSnapshot> snapshots,
        CancellationToken ct = default)
    {
        if (snapshots.Count == 0) return;
        if (!_schemaReady)
        {
            await EnsureSchemaAsync(ct);
        }

        const string sql = """
            INSERT INTO market_watch_live
                (exchange, symbol, last_price, last_price_ts, candles_json, updated_at)
            VALUES
                (@Exchange, @Symbol, @LastPrice, @LastPriceTimestampUtc, @CandlesJson::jsonb, now())
            ON CONFLICT (exchange, symbol)
            DO UPDATE SET
                last_price = EXCLUDED.last_price,
                last_price_ts = EXCLUDED.last_price_ts,
                candles_json = EXCLUDED.candles_json,
                updated_at = now();
            """;

        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, snapshots, cancellationToken: ct));
    }
}