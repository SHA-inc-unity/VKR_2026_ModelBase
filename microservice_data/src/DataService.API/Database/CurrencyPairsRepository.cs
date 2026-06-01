using Dapper;
using DataService.API.Dataset;
using Npgsql;

namespace DataService.API.Database;

/// <summary>
/// Single source of truth for the platform's currency pairs. Stores two
/// editable vocabularies — base assets and quote assets (stablecoins) — in the
/// <c>currency_pair_assets</c> table. The active trading pairs are the
/// cross-product of active bases × active quotes (e.g. BTC × USDT → BTCUSDT);
/// only those that actually list on an exchange are tracked (the market
/// watcher's discovery already intersects the configured set with reality).
///
/// The market watcher, dataset configuration and (via the gateway) the
/// frontend all derive their pair list from here instead of hardcoding it.
/// Mutations flow in over Kafka (cmd.data.pairs.*) — admin never writes the DB
/// directly. Schema is created idempotently in <see cref="EnsureSchemaAsync"/>
/// and seeded once from the legacy hardcoded universe so deploys don't break.
/// </summary>
public sealed class CurrencyPairsRepository
{
    public const string RoleBase  = "base";
    public const string RoleQuote = "quote";

    private static readonly string[] SeedQuotes = { "USDT" };
    private static readonly string[] QuoteStripOrder = { "USDT", "USDC", "USD" };

    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<CurrencyPairsRepository> _log;
    private readonly SemaphoreSlim _schemaGate = new(1, 1);
    private volatile bool _schemaReady;

    public CurrencyPairsRepository(PostgresConnectionFactory pg, ILogger<CurrencyPairsRepository> log)
    {
        _pg = pg;
        _log = log;
    }

    public sealed record PairAsset(string Asset, bool Active, int SortOrder);

    private sealed class AssetRow
    {
        public string Asset { get; set; } = string.Empty;
        public bool Active { get; set; }
        public int SortOrder { get; set; }
    }

    private const string CreateSchemaSql = """
        CREATE TABLE IF NOT EXISTS currency_pair_assets (
            role        TEXT        NOT NULL CHECK (role IN ('base','quote')),
            asset       TEXT        NOT NULL,
            active      BOOLEAN     NOT NULL DEFAULT TRUE,
            sort_order  INT         NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (role, asset)
        );
        """;

    public async Task EnsureSchemaAsync(CancellationToken ct = default)
    {
        if (_schemaReady) return;
        await _schemaGate.WaitAsync(ct);
        try
        {
            if (_schemaReady) return;
            await using var conn = await _pg.OpenAsync(ct);
            await conn.ExecuteAsync(new CommandDefinition(CreateSchemaSql, cancellationToken: ct));
            await SeedIfEmptyAsync(conn, ct);
            _schemaReady = true;
            _log.LogInformation("currency_pair_assets schema ensured");
        }
        finally { _schemaGate.Release(); }
    }

    private async Task SeedIfEmptyAsync(NpgsqlConnection conn, CancellationToken ct)
    {
        var count = await conn.ExecuteScalarAsync<long>(
            new CommandDefinition("SELECT count(*) FROM currency_pair_assets", cancellationToken: ct));
        if (count > 0) return;

        var bases = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var sym in DatasetConstants.SupportedSymbols)
        {
            var s = (sym ?? string.Empty).Trim().ToUpperInvariant();
            if (s.Length == 0) continue;
            var baseAsset = s;
            foreach (var q in QuoteStripOrder)
            {
                if (s.Length > q.Length && s.EndsWith(q, StringComparison.Ordinal)) { baseAsset = s[..^q.Length]; break; }
            }
            if (baseAsset.Length > 0) bases.Add(baseAsset);
        }

        var sort = 0;
        foreach (var b in bases.OrderBy(x => x, StringComparer.Ordinal))
        {
            await conn.ExecuteAsync(new CommandDefinition(
                "INSERT INTO currency_pair_assets(role, asset, active, sort_order) VALUES ('base', @Asset, TRUE, @Sort) ON CONFLICT (role, asset) DO NOTHING",
                new { Asset = b, Sort = sort++ }, cancellationToken: ct));
        }
        sort = 0;
        foreach (var q in SeedQuotes)
        {
            await conn.ExecuteAsync(new CommandDefinition(
                "INSERT INTO currency_pair_assets(role, asset, active, sort_order) VALUES ('quote', @Asset, TRUE, @Sort) ON CONFLICT (role, asset) DO NOTHING",
                new { Asset = q, Sort = sort++ }, cancellationToken: ct));
        }
        _log.LogInformation("Seeded currency_pair_assets: {Bases} bases, {Quotes} quotes", bases.Count, SeedQuotes.Length);
    }

    /// <summary>Validate + normalize an asset code (uppercase, A-Z0-9, ≤20 chars).</summary>
    public static string NormalizeAsset(string? asset)
    {
        var s = (asset ?? string.Empty).Trim().ToUpperInvariant();
        if (s.Length is 0 or > 20 || !s.All(c => (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')))
            throw new ArgumentException($"invalid asset code: '{asset}'");
        return s;
    }

    public static string NormalizeRole(string? role)
    {
        var r = (role ?? string.Empty).Trim().ToLowerInvariant();
        if (r != RoleBase && r != RoleQuote)
            throw new ArgumentException($"invalid role: '{role}' (expected 'base' or 'quote')");
        return r;
    }

    public async Task<IReadOnlyList<PairAsset>> ListAsync(string role, CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        var r = NormalizeRole(role);
        await using var conn = await _pg.OpenAsync(ct);
        var rows = await conn.QueryAsync<AssetRow>(new CommandDefinition(
            "SELECT asset AS \"Asset\", active AS \"Active\", sort_order AS \"SortOrder\" FROM currency_pair_assets WHERE role = @role ORDER BY sort_order, asset",
            new { role = r }, cancellationToken: ct));
        return rows.Select(x => new PairAsset(x.Asset, x.Active, x.SortOrder)).ToList();
    }

    public async Task AddAsync(string role, string asset, CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        var r = NormalizeRole(role);
        var a = NormalizeAsset(asset);
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition("""
            INSERT INTO currency_pair_assets(role, asset, active, sort_order, updated_at)
            VALUES (@role, @asset, TRUE,
                    COALESCE((SELECT max(sort_order) + 1 FROM currency_pair_assets WHERE role = @role), 0),
                    now())
            ON CONFLICT (role, asset) DO UPDATE SET active = TRUE, updated_at = now()
            """, new { role = r, asset = a }, cancellationToken: ct));
    }

    public async Task RemoveAsync(string role, string asset, CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        var r = NormalizeRole(role);
        var a = NormalizeAsset(asset);
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(
            "DELETE FROM currency_pair_assets WHERE role = @role AND asset = @asset",
            new { role = r, asset = a }, cancellationToken: ct));
    }

    public async Task SetActiveAsync(string role, string asset, bool active, CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        var r = NormalizeRole(role);
        var a = NormalizeAsset(asset);
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(
            "UPDATE currency_pair_assets SET active = @active, updated_at = now() WHERE role = @role AND asset = @asset",
            new { role = r, asset = a, active }, cancellationToken: ct));
    }

    /// <summary>
    /// Active trading symbols = cross-product of active base × active quote
    /// assets, concatenated (BTC + USDT → BTCUSDT). De-duplicated, uppercase.
    /// This is the authoritative configured-symbol set for the market watcher
    /// and dataset configuration.
    /// </summary>
    public async Task<IReadOnlyList<string>> GetActiveSymbolsAsync(CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        await using var conn = await _pg.OpenAsync(ct);
        var bases = (await conn.QueryAsync<string>(new CommandDefinition(
            "SELECT asset FROM currency_pair_assets WHERE role = 'base' AND active ORDER BY sort_order, asset",
            cancellationToken: ct))).ToList();
        var quotes = (await conn.QueryAsync<string>(new CommandDefinition(
            "SELECT asset FROM currency_pair_assets WHERE role = 'quote' AND active ORDER BY sort_order, asset",
            cancellationToken: ct))).ToList();

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var symbols = new List<string>(bases.Count * Math.Max(1, quotes.Count));
        foreach (var b in bases)
            foreach (var q in quotes)
            {
                var s = (b + q).ToUpperInvariant();
                if (seen.Add(s)) symbols.Add(s);
            }
        return symbols;
    }
}
