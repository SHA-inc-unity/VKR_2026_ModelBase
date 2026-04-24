using Dapper;
using Npgsql;
using DataService.API.Settings;
using Microsoft.Extensions.Options;

namespace DataService.API.Database;

/// <summary>
/// Creates and manages NpgsqlDataSource (connection pool) for the data service.
/// Port of Python db_pool.py.
/// </summary>
public sealed class PostgresConnectionFactory : IDisposable
{
    private readonly NpgsqlDataSource _dataSource;

    public PostgresConnectionFactory(IOptions<DataServiceSettings> opts)
    {
        var conn = opts.Value.Postgres.ConnectionString;
        _dataSource = NpgsqlDataSource.Create(conn);
    }

    /// <summary>Open a connection from the pool.</summary>
    public async Task<NpgsqlConnection> OpenAsync(CancellationToken ct = default) =>
        await _dataSource.OpenConnectionAsync(ct);

    /// <summary>Quick SELECT 1 round-trip to verify PostgreSQL is alive.</summary>
    public async Task<bool> PingAsync(CancellationToken ct = default)
    {
        await using var conn = await OpenAsync(ct);
        var result = await conn.QuerySingleAsync<int>("SELECT 1");
        return result == 1;
    }

    public void Dispose() => _dataSource.Dispose();
}
