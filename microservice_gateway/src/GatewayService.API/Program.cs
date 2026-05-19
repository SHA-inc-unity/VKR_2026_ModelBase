using GatewayService.API.Extensions;
using GatewayService.API.Kafka;
using GatewayService.API.Settings;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using GatewayService.API.Middleware;
using Serilog;

var builder = WebApplication.CreateBuilder(args);

// ── Logging ──────────────────────────────────────────────────────────────────
builder.Host.UseSerilog((ctx, cfg) => cfg.ReadFrom.Configuration(ctx.Configuration));

// ── Services ─────────────────────────────────────────────────────────────────
builder.Services.AddGatewayServices(builder.Configuration);
builder.Services.AddGatewaySwagger();
var healthChecks = builder.Services.AddHealthChecks()
    .AddCheck(
        "self",
        () => HealthCheckResult.Healthy(),
        tags: ["live", "ready"]);

if (!builder.Environment.IsEnvironment("Test"))
{
    healthChecks.AddCheck<KafkaBrokerHealthCheck>(
        "kafka",
        failureStatus: HealthStatus.Unhealthy,
        tags: ["ready"]);
}

// ── App ───────────────────────────────────────────────────────────────────────
var app = builder.Build();

app.UseMiddleware<CorrelationIdMiddleware>();
app.UseSerilogRequestLogging();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI(c =>
    {
        c.SwaggerEndpoint("/swagger/v1/swagger.json", "API Gateway v1");
        c.RoutePrefix = "swagger";
    });
}

app.UseMiddleware<GlobalExceptionMiddleware>();
app.UseCors(CorsSettings.PolicyName);
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
app.MapHealthChecks("/health", new HealthCheckOptions
{
    Predicate = check => check.Tags.Contains("live"),
});
app.MapHealthChecks("/health/ready", new HealthCheckOptions
{
    Predicate = check => check.Tags.Contains("ready"),
});

app.Run();

// Expose for WebApplicationFactory in integration tests.
public partial class Program { }
