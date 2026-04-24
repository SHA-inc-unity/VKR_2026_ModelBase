using DataService.API.Bybit;
using DataService.API.Database;
using DataService.API.HealthChecks;
using DataService.API.Kafka;
using DataService.API.Minio;
using DataService.API.Settings;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Serilog;

// ── Serilog bootstrap logger ──────────────────────────────────────────────
Log.Logger = new LoggerConfiguration()
    .WriteTo.Console()
    .CreateBootstrapLogger();

try
{
    var builder = WebApplication.CreateBuilder(args);

    // ── Serilog from appsettings ──────────────────────────────────────────
    builder.Host.UseSerilog((ctx, services, cfg) =>
        cfg.ReadFrom.Configuration(ctx.Configuration)
           .ReadFrom.Services(services)
           .Enrich.FromLogContext());

    // ── Configuration: env-var overrides ─────────────────────────────────
    // Map Docker env vars → DataService settings
    var cfg2 = builder.Configuration;
    if (Environment.GetEnvironmentVariable("PGHOST") is { } pgHost)
        cfg2["DataService:Postgres:Host"] = pgHost;
    if (Environment.GetEnvironmentVariable("PGPORT") is { } pgPort)
        cfg2["DataService:Postgres:Port"] = pgPort;
    if (Environment.GetEnvironmentVariable("PGDATABASE") is { } pgDb)
        cfg2["DataService:Postgres:Database"] = pgDb;
    if (Environment.GetEnvironmentVariable("PGUSER") is { } pgUser)
        cfg2["DataService:Postgres:User"] = pgUser;
    if (Environment.GetEnvironmentVariable("PGPASSWORD") is { } pgPass)
        cfg2["DataService:Postgres:Password"] = pgPass;
    if (Environment.GetEnvironmentVariable("KAFKA_BOOTSTRAP_SERVERS") is { } kafka)
        cfg2["DataService:Kafka:BootstrapServers"] = kafka;
    if (Environment.GetEnvironmentVariable("MINIO_ENDPOINT") is { } minioEp)
        cfg2["DataService:Minio:Endpoint"] = minioEp;
    if (Environment.GetEnvironmentVariable("MINIO_ACCESS_KEY") is { } minioAk)
        cfg2["DataService:Minio:AccessKey"] = minioAk;
    if (Environment.GetEnvironmentVariable("MINIO_SECRET_KEY") is { } minioSk)
        cfg2["DataService:Minio:SecretKey"] = minioSk;
    if (Environment.GetEnvironmentVariable("MINIO_BUCKET") is { } minioBk)
        cfg2["DataService:Minio:Bucket"] = minioBk;

    // ── Options ───────────────────────────────────────────────────────────
    builder.Services.Configure<DataServiceSettings>(
        builder.Configuration.GetSection("DataService"));

    // ── Infrastructure ────────────────────────────────────────────────────
    builder.Services.AddSingleton<PostgresConnectionFactory>();
    builder.Services.AddSingleton<DatasetRepository>();
    builder.Services.AddSingleton<KafkaProducer>();
    builder.Services.AddSingleton<MinioClaimCheckService>();

    // BybitApiClient via typed HttpClient
    builder.Services.AddHttpClient<BybitApiClient>(client =>
    {
        client.Timeout = TimeSpan.FromSeconds(DataService.API.Dataset.DatasetConstants.RequestTimeoutSeconds);
    });

    // ── Kafka consumer (hosted service) ───────────────────────────────────
    builder.Services.AddHostedService<KafkaConsumerService>();

    // ── Health checks ─────────────────────────────────────────────────────
    builder.Services.AddHealthChecks()
        .AddCheck<PostgresHealthCheck>("postgres", HealthStatus.Unhealthy,
            tags: ["ready"]);

    // ── MVC controllers ───────────────────────────────────────────────────
    builder.Services.AddControllers();

    // ── Build app ─────────────────────────────────────────────────────────
    var app = builder.Build();

    app.UseSerilogRequestLogging();

    app.MapControllers();

    // /health — always 200 (liveness)
    app.MapHealthChecks("/health", new HealthCheckOptions
    {
        Predicate = _ => false,   // skip all checks — just liveness
        ResultStatusCodes = { [HealthStatus.Healthy] = 200 },
    });

    // /ready — 200 only when Postgres is reachable
    app.MapHealthChecks("/ready", new HealthCheckOptions
    {
        Predicate = hc => hc.Tags.Contains("ready"),
        ResultStatusCodes =
        {
            [HealthStatus.Healthy]   = 200,
            [HealthStatus.Degraded]  = 200,
            [HealthStatus.Unhealthy] = 503,
        },
    });

    app.Run();
}
catch (Exception ex)
{
    Log.Fatal(ex, "Application terminated unexpectedly");
}
finally
{
    Log.CloseAndFlush();
}
