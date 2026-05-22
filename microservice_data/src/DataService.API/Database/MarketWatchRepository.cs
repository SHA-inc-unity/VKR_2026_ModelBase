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
    string? RealtimeSymbol,
    decimal LastPrice,
    DateTimeOffset LastPriceTimestampUtc,
    string CandlesJson);

public sealed record MarketWatchLiveRow(
    string Exchange,
    string Symbol,
    string? RealtimeSymbol,
    decimal LastPrice,
    DateTimeOffset LastPriceTimestampUtc,
    DateTimeOffset UpdatedAtUtc,
    string CandlesJson,
    long LagMs);

public sealed record MarketWatchLivePage(
    IReadOnlyList<MarketWatchLiveRow> Items,
    int Total,
    int Limit,
    int Offset);

public sealed class MarketWatchRepository
{
    private const string CreateSchemaSql = """
        CREATE TABLE IF NOT EXISTS market_watch_live (
            exchange       text        NOT NULL,
            symbol         text        NOT NULL,
            realtime_symbol text       NULL,
            last_price     numeric     NOT NULL,
            last_price_ts  timestamptz NOT NULL,
            candles_json   jsonb       NOT NULL DEFAULT '{}'::jsonb,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (exchange, symbol)
        );

        ALTER TABLE market_watch_live ADD COLUMN IF NOT EXISTS realtime_symbol text NULL;

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
                (exchange, symbol, realtime_symbol, last_price, last_price_ts, candles_json, updated_at)
            VALUES
                (@Exchange, @Symbol, @RealtimeSymbol, @LastPrice, @LastPriceTimestampUtc, @CandlesJson::jsonb, now())
            ON CONFLICT (exchange, symbol)
            DO UPDATE SET
                realtime_symbol = COALESCE(EXCLUDED.realtime_symbol, market_watch_live.realtime_symbol),
                last_price = EXCLUDED.last_price,
                last_price_ts = EXCLUDED.last_price_ts,
                candles_json = EXCLUDED.candles_json,
                updated_at = now();
            """;

        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(sql, snapshots, cancellationToken: ct));
    }

    public async Task<IReadOnlyList<Markets.MarketWatchSymbol>> ListKnownSymbolsAsync(
        string exchange,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(exchange)) return Array.Empty<Markets.MarketWatchSymbol>();
        if (!_schemaReady)
        {
            await EnsureSchemaAsync(ct);
        }

        const string sql = """
            SELECT symbol, realtime_symbol
            FROM market_watch_live
            WHERE exchange = @Exchange
            ORDER BY symbol
            """;

        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<(string Symbol, string? RealtimeSymbol)>(
            new CommandDefinition(sql, new { Exchange = exchange }, cancellationToken: ct));
        return rows
            .Select(row => new Markets.MarketWatchSymbol(row.Symbol, row.RealtimeSymbol))
            .ToArray();
    }

    public async Task<MarketWatchLivePage> ReadLivePageAsync(
        string? exchange,
        string? search,
        int limit,
        int offset,
        CancellationToken ct = default)
    {
        if (!_schemaReady)
        {
            await EnsureSchemaAsync(ct);
        }

        var safeLimit = Math.Clamp(limit, 1, 500);
        var safeOffset = Math.Max(offset, 0);
        var normalizedExchange = string.IsNullOrWhiteSpace(exchange)
            ? null
            : exchange.Trim().ToLowerInvariant();
        var normalizedSearch = string.IsNullOrWhiteSpace(search)
            ? null
            : search.Trim();

        const string sql = """
            SELECT
                exchange,
                symbol,
                realtime_symbol,
                last_price,
                last_price_ts,
                updated_at,
                candles_json::text AS candles_json,
                GREATEST(0, (EXTRACT(EPOCH FROM (now() - last_price_ts)) * 1000)::bigint) AS lag_ms
            FROM market_watch_live
            WHERE (@Exchange IS NULL OR exchange = @Exchange)
              AND (
                    @Search IS NULL
                 OR symbol ILIKE @LikeSearch
                 OR exchange ILIKE @LikeSearch
              )
            ORDER BY exchange, symbol
            LIMIT @Limit OFFSET @Offset;

            SELECT COUNT(*)
            FROM market_watch_live
            WHERE (@Exchange IS NULL OR exchange = @Exchange)
              AND (
                    @Search IS NULL
                 OR symbol ILIKE @LikeSearch
                 OR exchange ILIKE @LikeSearch
              );
            """;

        var args = new
        {
            Exchange = normalizedExchange,
            Search = normalizedSearch,
            LikeSearch = normalizedSearch is null ? null : $"%{normalizedSearch}%",
            Limit = safeLimit,
            Offset = safeOffset,
        };

        await using var conn = await _pg.OpenAsync(ct);
        using var multi = await conn.QueryMultipleAsync(new CommandDefinition(sql, args, cancellationToken: ct));
        var items = (await multi.ReadAsync<MarketWatchLiveRow>()).ToArray();
        var total = await multi.ReadFirstAsync<int>();
        return new MarketWatchLivePage(items, total, safeLimit, safeOffset);
    }
}