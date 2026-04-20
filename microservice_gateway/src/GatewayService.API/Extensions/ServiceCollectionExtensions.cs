using System.Text;
using GatewayService.API.Aggregators.Bootstrap;
using GatewayService.API.Middleware;
using GatewayService.API.Aggregators.Dashboard;
using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Market;
using GatewayService.API.Clients.News;
using GatewayService.API.Clients.Notifications;
using GatewayService.API.Clients.Portfolio;
using GatewayService.API.Settings;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.Extensions.Http.Resilience;
using Microsoft.IdentityModel.Tokens;
using Microsoft.OpenApi.Models;

namespace GatewayService.API.Extensions;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddGatewayServices(
        this IServiceCollection services,
        IConfiguration configuration)
    {
        // Settings
        services.Configure<DownstreamServicesSettings>(
            configuration.GetSection(DownstreamServicesSettings.SectionName));
        services.Configure<JwtSettings>(
            configuration.GetSection(JwtSettings.SectionName));
        services.Configure<FeatureFlagsSettings>(
            configuration.GetSection(FeatureFlagsSettings.SectionName));
        services.Configure<ResilienceSettings>(
            configuration.GetSection(ResilienceSettings.SectionName));

        var downstream = configuration
            .GetSection(DownstreamServicesSettings.SectionName)
            .Get<DownstreamServicesSettings>() ?? new();

        // Typed HTTP clients with resilience pipelines
        services
            .AddHttpClient<IAccountServiceClient, AccountServiceClient>(c =>
                ConfigureBase(c, downstream.Account.BaseUrl))
            .AddStandardResilienceHandler(opt => ConfigureResilience(opt, configuration));

        // Stub clients — no HttpClient needed (no real HTTP calls yet)
        services.AddTransient<IPortfolioServiceClient, PortfolioServiceClient>();
        services.AddTransient<IMarketServiceClient, MarketServiceClient>();
        services.AddTransient<INewsServiceClient, NewsServiceClient>();
        services.AddTransient<INotificationsServiceClient, NotificationsServiceClient>();

        // Aggregators
        services.AddScoped<IBootstrapAggregator, BootstrapAggregator>();
        services.AddScoped<IDashboardAggregator, DashboardAggregator>();

        // Auth
        var jwtSettings = configuration.GetSection(JwtSettings.SectionName).Get<JwtSettings>() ?? new();
        services
            .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
            .AddJwtBearer(options =>
            {
                options.TokenValidationParameters = new TokenValidationParameters
                {
                    ValidateIssuerSigningKey = true,
                    IssuerSigningKey = new SymmetricSecurityKey(
                        Encoding.UTF8.GetBytes(jwtSettings.SecretKey)),
                    ValidateIssuer = true,
                    ValidIssuer = jwtSettings.Issuer,
                    ValidateAudience = true,
                    ValidAudience = jwtSettings.Audience,
                    ClockSkew = TimeSpan.FromSeconds(30)
                };

                options.Events = new JwtBearerEvents
                {
                    OnChallenge = ctx =>
                    {
                        ctx.HandleResponse();
                        ctx.Response.StatusCode = 401;
                        ctx.Response.ContentType = "application/json";
                        var correlationId = ctx.HttpContext.GetCorrelationId();
                        return ctx.Response.WriteAsJsonAsync(DTOs.ErrorResponse.Unauthorized(correlationId));
                    }
                };
            });

        services.AddAuthorization();
        services.AddControllers();

        return services;
    }

    public static IServiceCollection AddGatewaySwagger(this IServiceCollection services)
    {
        services.AddEndpointsApiExplorer();
        services.AddSwaggerGen(c =>
        {
            c.SwaggerDoc("v1", new OpenApiInfo
            {
                Title = "Exchange App — API Gateway",
                Version = "v1",
                Description = "Mobile BFF / API Gateway for the Exchange App Flutter client."
            });

            var securityScheme = new OpenApiSecurityScheme
            {
                Name = "Authorization",
                Type = SecuritySchemeType.Http,
                Scheme = "Bearer",
                BearerFormat = "JWT",
                In = ParameterLocation.Header,
                Description = "Enter the JWT access token from Account Service login."
            };
            c.AddSecurityDefinition("Bearer", securityScheme);
            c.AddSecurityRequirement(new OpenApiSecurityRequirement
            {
                {
                    new OpenApiSecurityScheme
                    {
                        Reference = new OpenApiReference
                        {
                            Type = ReferenceType.SecurityScheme,
                            Id = "Bearer"
                        }
                    },
                    Array.Empty<string>()
                }
            });
        });

        return services;
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private static void ConfigureBase(HttpClient client, string baseUrl)
    {
        if (!string.IsNullOrEmpty(baseUrl))
            client.BaseAddress = new Uri(baseUrl.TrimEnd('/') + '/');
    }

    private static void ConfigureResilience(HttpStandardResilienceOptions opt, IConfiguration configuration)
    {
        var s = configuration.GetSection(ResilienceSettings.SectionName).Get<ResilienceSettings>() ?? new();

        opt.Retry.MaxRetryAttempts = s.RetryCount;
        opt.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(s.TimeoutSeconds * (s.RetryCount + 1));
        opt.AttemptTimeout.Timeout = TimeSpan.FromSeconds(s.TimeoutSeconds);
        opt.CircuitBreaker.MinimumThroughput = s.CircuitBreakerFailureThreshold;
        opt.CircuitBreaker.BreakDuration = TimeSpan.FromSeconds(s.CircuitBreakerDurationSeconds);
    }
}
