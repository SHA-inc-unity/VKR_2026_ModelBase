using System.Text;
using Dapper;
using Npgsql;

namespace DataService.API.Database;

public sealed partial class DatasetRepository
{
    // ── Audit log query ──────────────────────────────────────────────────
    public sealed record AuditLogEntry(
        int Id, string TableName, string Operation, string ParamsJson,
        long RowsAffected, DateTime AppliedAt);

    /// <summary>
    /// Return the last <paramref name="limit"/> audit-log rows for the given
    /// table (or for all tables when <paramref name="tableName"/> is null).
    /// </summary>
    public async Task<IReadOnlyList<AuditLogEntry>> GetAuditLogAsync(
        string? tableName, int limit, CancellationToken ct = default)
    {
        if (limit <= 0)   limit = 50;
        if (limit > 500)  limit = 500;
        await using var conn = await _pg.OpenAsync(ct);
        // The audit-log table is created lazily on first apply, so guard.
        var exists = await conn.ExecuteScalarAsync<bool>(new CommandDefinition(
            @"SELECT EXISTS (SELECT 1 FROM information_schema.tables
                              WHERE table_schema='public' AND table_name='dataset_audit_log')",
            cancellationToken: ct));
        if (!exists) return Array.Empty<AuditLogEntry>();
        var sql = string.IsNullOrWhiteSpace(tableName)
            ? @"SELECT id, table_name, operation, params::text AS paramsjson,
                       rows_affected, applied_at
                FROM dataset_audit_log
                ORDER BY applied_at DESC LIMIT @lim"
            : @"SELECT id, table_name, operation, params::text AS paramsjson,
                       rows_affected, applied_at
                FROM dataset_audit_log
                WHERE table_name = @tbl
                ORDER BY applied_at DESC LIMIT @lim";
        var rows = await conn.QueryAsync<(int id, string table_name, string operation,
            string paramsjson, int rows_affected, DateTime applied_at)>(
            new CommandDefinition(sql, new { tbl = tableName, lim = limit }, cancellationToken: ct));
        return rows.Select(r => new AuditLogEntry(
            r.id, r.table_name, r.operation, r.paramsjson ?? "{}",
            r.rows_affected, r.applied_at)).ToList();
    }

    // ── Audit log ─────────────────────────────────────────────────────────

    /// <summary>
    /// Idempotently create the dataset_audit_log table on first apply call.
    /// Schema is intentionally simple: no FK to any data table — the log
    /// outlives its referenced tables, and rolling back DDL would orphan rows
    /// here anyway.
    /// </summary>
    public async Task EnsureAuditLogAsync(CancellationToken ct = default)
    {
        await using var conn = await _pg.OpenAsync(ct);
        await conn.ExecuteAsync(new CommandDefinition(@"
            CREATE TABLE IF NOT EXISTS dataset_audit_log (
                id             SERIAL PRIMARY KEY,
                table_name     VARCHAR     NOT NULL,
                operation      VARCHAR     NOT NULL,
                params         JSONB       NOT NULL,
                rows_affected  INTEGER     NOT NULL DEFAULT 0,
                applied_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS dataset_audit_log_table_idx
                ON dataset_audit_log(table_name, applied_at DESC);
        ", cancellationToken: ct));
    }

    public async Task<int> WriteAuditLogAsync(
        string tableName, string operation, string paramsJson,
        long rowsAffected, CancellationToken ct = default)
    {
        await using var conn = await _pg.OpenAsync(ct);
        return await conn.ExecuteScalarAsync<int>(new CommandDefinition(
            @"INSERT INTO dataset_audit_log(table_name, operation, params, rows_affected)
              VALUES (@table_name, @operation, @params::jsonb, @rows_affected)
              RETURNING id",
            new { table_name = tableName, operation, @params = paramsJson, rows_affected = (int)rowsAffected },
            cancellationToken: ct));
    }
}
