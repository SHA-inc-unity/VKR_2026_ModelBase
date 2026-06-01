using System.Text;
using System.Text.RegularExpressions;
using Dapper;
using DataService.API.Dataset;
using Npgsql;
using NpgsqlTypes;

namespace DataService.API.Database;

/// <summary>
/// All PostgreSQL dataset operations.
///
/// Schema (market data tables are named "{symbol}_{timeframe}", e.g. "btcusdt_5m"):
///   timestamp_utc   TIMESTAMP WITH TIME ZONE PRIMARY KEY
///   symbol          VARCHAR
///   exchange        VARCHAR
///   timeframe       VARCHAR
///   close_price     NUMERIC   (close price)
///   open_price      NUMERIC
///   high_price      NUMERIC
///   low_price       NUMERIC
///   volume          NUMERIC
///   turnover        NUMERIC
///   funding_rate    NUMERIC
///   open_interest   NUMERIC
///   rsi             NUMERIC
///   … + 37 feature columns (double precision, nullable)
///
/// All timestamps crossing the Kafka boundary are epoch milliseconds.
/// </summary>
public sealed partial class DatasetRepository
{
    private readonly PostgresConnectionFactory _pg;
    private readonly ILogger<DatasetRepository> _log;
    private const int NoTimeout = 0;

    // Only lower-case letters, digits, underscores are safe in identifiers.
    private static readonly Regex _safeIdentifier =
        new(@"^[a-z0-9_]+$", RegexOptions.Compiled);

    public DatasetRepository(PostgresConnectionFactory pg, ILogger<DatasetRepository> log)
    {
        _pg  = pg;
        _log = log;
    }

    // ── Identifier guard ──────────────────────────────────────────────────
    private static string Safe(string name)
    {
        var n = name.Trim().ToLowerInvariant();
        if (!_safeIdentifier.IsMatch(n))
            throw new ArgumentException($"Unsafe SQL identifier: '{name}'");
        return n;
    }

    private static DateTime ToUtc(long ms) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ms).UtcDateTime;

    private static long ToMs(DateTime utc) =>
        new DateTimeOffset(DateTime.SpecifyKind(utc, DateTimeKind.Utc)).ToUnixTimeMilliseconds();

    // ── Ping ──────────────────────────────────────────────────────────────

    public Task<bool> PingAsync(CancellationToken ct = default) => _pg.PingAsync(ct);
}

internal static class DictionaryExtensions
{
    public static IReadOnlyDictionary<K, V> AsReadOnly<K, V>(this IDictionary<K, V> d)
        where K : notnull => new System.Collections.ObjectModel.ReadOnlyDictionary<K, V>(d);
}
