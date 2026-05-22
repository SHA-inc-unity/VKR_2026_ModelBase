using DataService.API.Database;
using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace DataService.API.HealthChecks;

public sealed class PostgresHealthCheck : IHealthCheck
{
    private readonly PostgresConnectionFactory _pg;

    public PostgresHealthCheck(PostgresConnectionFactory pg) => _pg = pg;

    public async Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context, CancellationToken ct = default)
    {
        try
        {
            await _pg.PingAsync(ct);
            return HealthCheckResult.Healthy("PostgreSQL reachable");
        }
        catch (Exception ex)
        {
            return HealthCheckResult.Unhealthy("PostgreSQL unreachable", ex);
        }
    }
}
