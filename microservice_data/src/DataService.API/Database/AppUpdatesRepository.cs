using System.Text.Json;
using Dapper;
using Npgsql;

namespace DataService.API.Database;

/// <summary>
/// Append-only store for the in-app "app updates / changelog" history that the
/// client renders on its Updates screen. Two tables hold the data:
///
/// <list type="bullet">
///   <item><c>app_update_release</c> — one row per public release (version,
///   title, display date, public highlight bullets, sort index).</item>
///   <item><c>app_update_change</c> — one row per per-build change item
///   (admin-visible detail), keyed by version + build number.</item>
/// </list>
///
/// The store is <b>append-only at the database level</b>: a plpgsql trigger
/// guard (<c>app_updates_block_mutations</c>) raises on any UPDATE / DELETE /
/// TRUNCATE against either table, so the changelog is an immutable audit trail
/// even for the table owner. New entries are added via INSERT-only helpers
/// (<see cref="AppendReleaseAsync"/> / <see cref="AppendChangeAsync"/>).
///
/// Schema is created idempotently in <see cref="EnsureSchemaAsync"/> and seeded
/// once from <see cref="AppUpdatesSeed"/> (a faithful mirror of the Flutter
/// client's bundled <c>kAppReleases</c>). Reads (<see cref="ListAsync"/>) build
/// the nested releases→builds→changes JSON shape consumed over
/// <c>cmd.data.updates.list</c>. Mirrors the EnsureSchema/Seed pattern of
/// <see cref="CurrencyPairsRepository"/>.
/// </summary>
public sealed class AppUpdatesRepository
{
    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<AppUpdatesRepository> _log;
    private readonly SemaphoreSlim _schemaGate = new(1, 1);
    private volatile bool _schemaReady;

    public AppUpdatesRepository(PostgresConnectionFactory pg, ILogger<AppUpdatesRepository> log)
    {
        _pg = pg;
        _log = log;
    }

    public bool SchemaReady => _schemaReady;

    // ── DTOs whose JSON serialization matches the cmd.data.updates.list contract ──

    /// <summary>JSON: { type, scope, text }.</summary>
    public sealed record ChangeDto(string type, string scope, string text);

    /// <summary>JSON: { build, date, changes }.</summary>
    public sealed record BuildDto(int build, string date, IReadOnlyList<ChangeDto> changes);

    /// <summary>JSON: { version, date, title, highlights, builds }.</summary>
    public sealed record ReleaseDto(
        string version,
        string date,
        string title,
        IReadOnlyList<string> highlights,
        IReadOnlyList<BuildDto> builds);

    // ── Schema ──────────────────────────────────────────────────────────────

    private const string CreateSchemaSql = """
        CREATE TABLE IF NOT EXISTS app_update_release (
            id           BIGSERIAL    PRIMARY KEY,
            version      VARCHAR(32)  NOT NULL UNIQUE,
            title        VARCHAR(256) NOT NULL,
            release_date VARCHAR(32)  NOT NULL,
            highlights   JSONB        NOT NULL DEFAULT '[]',
            sort_index   INT          NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS app_update_change (
            id           BIGSERIAL    PRIMARY KEY,
            version      VARCHAR(32)  NOT NULL,
            build_number INT          NOT NULL,
            build_date   VARCHAR(32)  NOT NULL,
            change_type  VARCHAR(16)  NOT NULL,
            scope        VARCHAR(16)  NOT NULL,
            change_text  TEXT         NOT NULL,
            sort_index   INT          NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS app_update_change_ver_idx
            ON app_update_change(version, build_number DESC, sort_index);

        -- Append-only guard: block UPDATE/DELETE/TRUNCATE even for the owner.
        CREATE OR REPLACE FUNCTION app_updates_block_mutations() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table %: % is not allowed', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS app_update_release_no_mutation ON app_update_release;
        CREATE TRIGGER app_update_release_no_mutation
            BEFORE UPDATE OR DELETE ON app_update_release
            FOR EACH ROW EXECUTE FUNCTION app_updates_block_mutations();
        DROP TRIGGER IF EXISTS app_update_release_no_truncate ON app_update_release;
        CREATE TRIGGER app_update_release_no_truncate
            BEFORE TRUNCATE ON app_update_release
            FOR EACH STATEMENT EXECUTE FUNCTION app_updates_block_mutations();

        DROP TRIGGER IF EXISTS app_update_change_no_mutation ON app_update_change;
        CREATE TRIGGER app_update_change_no_mutation
            BEFORE UPDATE OR DELETE ON app_update_change
            FOR EACH ROW EXECUTE FUNCTION app_updates_block_mutations();
        DROP TRIGGER IF EXISTS app_update_change_no_truncate ON app_update_change;
        CREATE TRIGGER app_update_change_no_truncate
            BEFORE TRUNCATE ON app_update_change
            FOR EACH STATEMENT EXECUTE FUNCTION app_updates_block_mutations();
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
            _log.LogInformation("app_update_release/app_update_change schema ensured");
        }
        finally { _schemaGate.Release(); }
    }

    private async Task SeedIfEmptyAsync(NpgsqlConnection conn, CancellationToken ct)
    {
        var count = await conn.ExecuteScalarAsync<long>(
            new CommandDefinition("SELECT count(*) FROM app_update_release", cancellationToken: ct));
        if (count > 0) return;

        var releaseRows = 0;
        var changeRows = 0;
        foreach (var rel in AppUpdatesSeed.Releases)
        {
            var highlightsJson = JsonSerializer.Serialize(rel.Highlights);
            // Release: ON CONFLICT (version) DO NOTHING — never overwrite.
            await conn.ExecuteAsync(new CommandDefinition("""
                INSERT INTO app_update_release(version, title, release_date, highlights, sort_index)
                VALUES (@version, @title, @release_date, @highlights::jsonb, @sort_index)
                ON CONFLICT (version) DO NOTHING
                """,
                new
                {
                    version = rel.Version,
                    title = rel.Title,
                    release_date = rel.ReleaseDate,
                    highlights = highlightsJson,
                    sort_index = rel.SortIndex,
                }, cancellationToken: ct));
            releaseRows++;

            foreach (var build in rel.Builds)
            {
                foreach (var change in build.Changes)
                {
                    await conn.ExecuteAsync(new CommandDefinition("""
                        INSERT INTO app_update_change(version, build_number, build_date, change_type, scope, change_text, sort_index)
                        VALUES (@version, @build_number, @build_date, @change_type, @scope, @change_text, @sort_index)
                        """,
                        new
                        {
                            version = rel.Version,
                            build_number = build.BuildNumber,
                            build_date = build.BuildDate,
                            change_type = change.Type,
                            scope = change.Scope,
                            change_text = change.Text,
                            sort_index = change.SortIndex,
                        }, cancellationToken: ct));
                    changeRows++;
                }
            }
        }

        _log.LogInformation("Seeded app updates: {Releases} releases, {Changes} change rows",
            releaseRows, changeRows);
    }

    // ── Append-only writers (for future appends) ─────────────────────────────

    /// <summary>
    /// Insert a new release. ON CONFLICT (version) DO NOTHING — the store is
    /// append-only, so an existing version is left untouched (the trigger guard
    /// would block any update anyway).
    /// </summary>
    public async Task AppendReleaseAsync(
        string version, string title, string releaseDate,
        IReadOnlyList<string> highlights, int sortIndex, CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        var highlightsJson = JsonSerializer.Serialize(highlights ?? Array.Empty<string>());
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition("""
            INSERT INTO app_update_release(version, title, release_date, highlights, sort_index)
            VALUES (@version, @title, @release_date, @highlights::jsonb, @sort_index)
            ON CONFLICT (version) DO NOTHING
            """,
            new
            {
                version,
                title,
                release_date = releaseDate,
                highlights = highlightsJson,
                sort_index = sortIndex,
            }, cancellationToken: ct));
    }

    /// <summary>Insert a single per-build change item (plain append).</summary>
    public async Task AppendChangeAsync(
        string version, int buildNumber, string buildDate,
        string changeType, string scope, string changeText, int sortIndex,
        CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition("""
            INSERT INTO app_update_change(version, build_number, build_date, change_type, scope, change_text, sort_index)
            VALUES (@version, @build_number, @build_date, @change_type, @scope, @change_text, @sort_index)
            """,
            new
            {
                version,
                build_number = buildNumber,
                build_date = buildDate,
                change_type = changeType,
                scope,
                change_text = changeText,
                sort_index = sortIndex,
            }, cancellationToken: ct));
    }

    // ── Read ──────────────────────────────────────────────────────────────

    private sealed class ReleaseRow
    {
        public string Version { get; set; } = string.Empty;
        public string Title { get; set; } = string.Empty;
        public string ReleaseDate { get; set; } = string.Empty;
        public string Highlights { get; set; } = "[]";
        public int SortIndex { get; set; }
    }

    private sealed class ChangeRow
    {
        public string Version { get; set; } = string.Empty;
        public int BuildNumber { get; set; }
        public string BuildDate { get; set; } = string.Empty;
        public string ChangeType { get; set; } = string.Empty;
        public string Scope { get; set; } = string.Empty;
        public string ChangeText { get; set; } = string.Empty;
        public int SortIndex { get; set; }
    }

    /// <summary>
    /// Read both tables and build the nested releases (newest first). Releases
    /// are ordered by sort_index DESC; within a release, builds are grouped by
    /// build_number ordered DESC (newest build first); within a build, changes
    /// are in authored order (sort_index ASC). The returned DTOs serialize to
    /// the exact cmd.data.updates.list contract shape.
    /// </summary>
    public async Task<IReadOnlyList<ReleaseDto>> ListAsync(CancellationToken ct = default)
    {
        await EnsureSchemaAsync(ct);
        await using var conn = await _pg.OpenAsync(ct);

        var releaseRows = (await conn.QueryAsync<ReleaseRow>(new CommandDefinition("""
            SELECT version      AS "Version",
                   title        AS "Title",
                   release_date AS "ReleaseDate",
                   highlights   AS "Highlights",
                   sort_index   AS "SortIndex"
            FROM app_update_release
            ORDER BY sort_index DESC
            """, cancellationToken: ct))).ToList();

        var changeRows = (await conn.QueryAsync<ChangeRow>(new CommandDefinition("""
            SELECT version      AS "Version",
                   build_number AS "BuildNumber",
                   build_date   AS "BuildDate",
                   change_type  AS "ChangeType",
                   scope        AS "Scope",
                   change_text  AS "ChangeText",
                   sort_index   AS "SortIndex"
            FROM app_update_change
            ORDER BY version, build_number DESC, sort_index ASC
            """, cancellationToken: ct))).ToList();

        // version -> ordered builds (newest build first), each preserving the
        // authored change order from the ORDER BY above.
        var buildsByVersion = new Dictionary<string, List<BuildDto>>(StringComparer.Ordinal);
        var buildIndexByVersion = new Dictionary<string, Dictionary<int, int>>(StringComparer.Ordinal);

        foreach (var c in changeRows)
        {
            if (!buildsByVersion.TryGetValue(c.Version, out var builds))
            {
                builds = new List<BuildDto>();
                buildsByVersion[c.Version] = builds;
                buildIndexByVersion[c.Version] = new Dictionary<int, int>();
            }
            var idx = buildIndexByVersion[c.Version];
            if (!idx.TryGetValue(c.BuildNumber, out var pos))
            {
                pos = builds.Count;
                builds.Add(new BuildDto(c.BuildNumber, c.BuildDate, new List<ChangeDto>()));
                idx[c.BuildNumber] = pos;
            }
            ((List<ChangeDto>)builds[pos].changes).Add(new ChangeDto(c.ChangeType, c.Scope, c.ChangeText));
        }

        var releases = new List<ReleaseDto>(releaseRows.Count);
        foreach (var r in releaseRows)
        {
            var highlights = ParseHighlights(r.Highlights);
            var builds = buildsByVersion.TryGetValue(r.Version, out var b)
                ? (IReadOnlyList<BuildDto>)b
                : Array.Empty<BuildDto>();
            releases.Add(new ReleaseDto(r.Version, r.ReleaseDate, r.Title, highlights, builds));
        }
        return releases;
    }

    private static IReadOnlyList<string> ParseHighlights(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return Array.Empty<string>();
        try
        {
            var arr = JsonSerializer.Deserialize<List<string>>(json);
            return arr ?? new List<string>();
        }
        catch
        {
            return Array.Empty<string>();
        }
    }
}
