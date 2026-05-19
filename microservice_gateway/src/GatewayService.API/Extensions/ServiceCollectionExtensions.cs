using System.Text;
using GatewayService.API.Aggregators.Bootstrap;
using GatewayService.API.Aggregators.Dashboard;
using GatewayService.API.Filters;
using GatewayService.API.Middleware;
using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Market;
using GatewayService.API.Clients.News;
using GatewayService.API.Clients.Notifications;
using GatewayService.API.Clients.Portfolio;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using GatewayService.API.Settings;
using Microsoft.AspNetCore.Authentication.JwtBearer;
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
        services.Configure<KafkaSettings>(
            configuration.GetSection(KafkaSettings.SectionName));
        services.Configure<MarketSettings>(
            configuration.GetSection(MarketSettings.SectionName));
        services.Configure<AdminSettings>(
            configuration.GetSection("Admin"));

        // Admin API key filter — scoped so IOptions<AdminSettings> resolves cleanly
        services.AddScoped<AdminApiKeyFilter>();

        // Kafka request/reply — singleton + hosted service for the consume loop
        services.AddSingleton<KafkaRequestClient>();
        services.AddSingleton<IKafkaRequestClient>(sp => sp.GetRequiredService<KafkaRequestClient>());
        services.AddHostedService(sp => sp.GetRequiredService<KafkaRequestClient>());

        // Redis distributed cache (falls back to in-memory when Redis section is absent)
        var redisConfig = configuration["Redis:Configuration"];
        if (!string.IsNullOrWhiteSpace(redisConfig))
        {
            services.AddStackExchangeRedisCache(opts => opts.Configuration = redisConfig);
        }
        else
        {
            services.AddDistributedMemoryCache();
        }

        // Market API — Bybit HTTP client
        services.AddHttpClient(nameof(BybitSymbolProvider))
                .ConfigureHttpClient(c => c.Timeout = TimeSpan.FromSeconds(15));

        // Market API — services
        // All market services are singletons: they delegate to singleton Redis/Kafka,
        // and IHttpClientFactory (used by BybitSymbolProvider) is singleton-safe.
        services.AddSingleton<IBybitSymbolProvider, BybitSymbolProvider>();
        services.AddSingleton<IMarketCacheService, MarketCacheService>();
        services.AddSingleton<IDataServiceClient, DataServiceClient>();
        services.AddSingleton<IMarketConfigService, MarketConfigService>();
        // ChartService registered as concrete type so ChartRequestQueue can inject it.
        services.AddSingleton<ChartService>();
        // ChartRequestQueue is the IChartService — provides coalescing + concurrency limits.
        services.AddSingleton<IChartService, ChartRequestQueue>();

        // Account client — Kafka-backed (no HttpClient)
        services.AddScoped<IAccountServiceClient, AccountServiceClient>();

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

}
