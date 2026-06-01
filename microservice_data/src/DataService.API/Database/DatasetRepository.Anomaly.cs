using System.Text;
using Dapper;
using Npgsql;

namespace DataService.API.Database;

/// <summary>
/// Anomaly-detection and clean (preview/apply) operations for market-data tables.
///
/// Detection methods are pure SQL — each returns a list of anomaly records
/// shaped <c>{ts_ms, anomaly_type, severity, column, value, details}</c>.
/// Clean methods either count (<c>*Preview*</c>) or mutate
/// (<c>*Apply*</c>) the table and emit an audit-log entry.
///
/// Severity rules:
///   • critical → duplicates, OHLC violations, negative values
///   • warning  → gaps, zero/null streaks, IQR outliers, Z-score outliers
///
/// All apply operations are wrapped in <c>pg_advisory_lock</c> at the call site
/// to serialise concurrent <c>cmd.data.dataset.clean.apply</c> requests.
///
/// This anomaly surface is split across several partial files in the same
/// directory/namespace:
///   • DatasetRepository.Anomaly.cs        — shared records, column whitelists,
///                                            and helpers used across the group
///   • DatasetRepository.Anomaly.Detect.cs — pure-SQL Detect* methods
///   • DatasetRepository.Anomaly.Audit.cs  — audit-log read/write
///   • DatasetRepository.Anomaly.Clean.cs  — clean preview counts + apply
///                                            mutations + advisory-lock helpers
/// </summary>
public sealed partial class DatasetRepository
{
    public sealed record AnomalyRow(
        long    TsMs,
        string  AnomalyType,
        string  Severity,
        string? Column,
        double? Value,
        string? Details);

    public sealed record AnomalyDetectionResult(
        long Total,
        long Critical,
        long Warning,
        Dictionary<string, long> ByType,
        IReadOnlyList<AnomalyRow> Rows);

    // ── Whitelisted columns for anomaly checks ────────────────────────────
    // Keep this in sync with DatasetConstants.RawTableSchema; we only inspect
    // semantically meaningful raw columns, never feature columns.
    private static readonly string[] _ohlcCols     = { "open_price", "high_price", "low_price", "close_price" };
    private static readonly string[] _negativeCols = { "open_price", "high_price", "low_price", "close_price", "volume", "turnover", "open_interest" };
    private static readonly string[] _streakCols   = { "open_interest", "funding_rate" };
    private static readonly string[] _outlierCols  = { "close_price", "volume", "turnover", "open_interest" };

    private async Task<HashSet<string>> GetColumnNamesAsync(string tbl, CancellationToken ct)
    {
        await using var conn = await _pg.OpenAsync(ct);
        var names = await conn.QueryAsync<string>(new CommandDefinition(
            @"SELECT column_name FROM information_schema.columns
              WHERE table_schema = 'public' AND table_name = @tbl",
            new { tbl }, cancellationToken: ct));
        return new HashSet<string>(names, StringComparer.OrdinalIgnoreCase);
    }

    public async Task<long> GetRowCountAsync(string tableName, CancellationToken ct = default)
    {
        if (!await TableExistsAsync(tableName, ct)) return 0;
        var tbl = Safe(tableName);
        await using var conn = await _pg.OpenAsync(ct);
        return await conn.ExecuteScalarAsync<long>(new CommandDefinition(
            $@"SELECT COUNT(*)::bigint FROM ""{tbl}""", cancellationToken: ct));
    }
}
