using System.Text;
using GatewayService.API.Aggregators.Bootstrap;
using GatewayService.API.Aggregators.Dashboard;
using GatewayService.API.Middleware;
using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Bybit;
using GatewayService.API.Clients.Market;
using GatewayService.API.Clients.News;
using GatewayService.API.Clients.Notifications;
using GatewayService.API.Clients.Portfolio;
using GatewayService.API.Clients.Social;
using GatewayService.API.Frontend;
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
        services.Configure<CorsSettings>(
            configuration.GetSection(CorsSettings.SectionName));
        services.Configure<AdminSettings>(
            configuration.GetSection("Admin"));

        services.AddMemoryCache();

        var corsSettings = configuration.GetSection(CorsSettings.SectionName).Get<CorsSettings>() ?? new();

        services.AddCors(options =>
        {
            options.AddPolicy(CorsSettings.PolicyName, policy =>
            {
                policy.AllowAnyHeader()
                      .AllowAnyMethod()
                      .SetPreflightMaxAge(TimeSpan.FromSeconds(corsSettings.PreflightMaxAgeSeconds));

                if (corsSettings.AllowAnyOrigin
                    || corsSettings.AllowedOrigins.Length == 0
                    || corsSettings.AllowedOrigins.Contains("*", StringComparer.Ordinal))
                {
                    policy.AllowAnyOrigin();
                    return;
                }

                policy.WithOrigins(corsSettings.AllowedOrigins);
            });
        });

        // Kafka request/reply — singleton + hosted service for the consume loop
        services.AddSingleton<KafkaRequestClient>();
        services.AddSingleton<IKafkaRequestClient>(sp => sp.GetRequiredService<KafkaRequestClient>());
        services.AddSingleton<IKafkaRequestClientProbe>(sp => sp.GetRequiredService<KafkaRequestClient>());
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
        services.AddHttpClient(nameof(MarketServiceClient))
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
        services.AddHttpClient<IAccountAuthProxyClient, AccountAuthProxyClient>(client =>
            {
                client.BaseAddress = BuildAccountServiceUri(configuration);
                client.Timeout = TimeSpan.FromSeconds(15);
            });

        // Account internal client (X-Internal-Api-Key) — used to fetch decrypted
        // exchange API keys for the user.
        var internalApiKey = configuration["InternalApi:ApiKey"]
                             ?? Environment.GetEnvironmentVariable("ACCOUNT_INTERNAL_SECRET")
                             ?? string.Empty;
        services.AddHttpClient<IAccountInternalClient, AccountInternalClient>(client =>
        {
            client.BaseAddress = BuildAccountServiceUri(configuration);
            client.Timeout = TimeSpan.FromSeconds(15);
            if (!string.IsNullOrWhiteSpace(internalApiKey))
                client.DefaultRequestHeaders.Add("X-Internal-Api-Key", internalApiKey);
        });

        // Bybit V5 private API (HMAC-signed wallet / fee endpoints).
        services.AddHttpClient<IBybitPrivateClient, BybitPrivateClient>(client =>
        {
            client.BaseAddress = new Uri("https://api.bybit.com");
            client.Timeout = TimeSpan.FromSeconds(15);
        });

        // Stub clients — no HttpClient needed (no real HTTP calls yet)
        services.AddSingleton<IFrontendContractState, FrontendContractState>();
        services.AddTransient<IPortfolioServiceClient, PortfolioServiceClient>();
        services.AddTransient<IMarketServiceClient, MarketServiceClient>();
        services.AddTransient<INewsServiceClient, NewsServiceClient>();
        services.AddTransient<INotificationsServiceClient, NotificationsServiceClient>();

        // Real HTTP proxy clients for social-stack microservices.
        services.AddHttpClient<ISocialServiceClient, SocialServiceClient>((sp, c) =>
        {
            c.BaseAddress = BuildDownstreamUri(configuration, "DownstreamServices:Social:BaseUrl", "SOCIAL_SERVICE_URL", "http://social_service_api:5000");
            c.Timeout = TimeSpan.FromSeconds(15);
        });
        services.AddHttpClient<INewsHttpProxyClient, NewsHttpProxyClient>((sp, c) =>
        {
            c.BaseAddress = BuildDownstreamUri(configuration, "DownstreamServices:News:BaseUrl", "NEWS_SERVICE_URL", "http://news_service_api:5000");
            c.Timeout = TimeSpan.FromSeconds(15);
        });
        services.AddHttpClient<INotificationsHttpProxyClient, NotificationsHttpProxyClient>((sp, c) =>
        {
            c.BaseAddress = BuildDownstreamUri(configuration, "DownstreamServices:Notifications:BaseUrl", "NOTIFICATION_SERVICE_URL", "http://notification_service_api:5000");
            // SSE is long-lived; we use a generous timeout. The actual request uses HttpCompletionOption.ResponseHeadersRead.
            c.Timeout = TimeSpan.FromMinutes(30);
        });

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
                    OnAuthenticationFailed = ctx =>
                    {
                        // Surface the underlying validation failure so we can diagnose
                        // mysterious 401s in production. Without this the OnChallenge
                        // handler eats the reason and the client just sees "401".
                        var logger = ctx.HttpContext.RequestServices
                            .GetRequiredService<ILoggerFactory>()
                            .CreateLogger("Gateway.JwtBearer");
                        logger.LogWarning(ctx.Exception,
                            "JWT auth failed for {Method} {Path}: {ExceptionType}: {Message}",
                            ctx.Request.Method,
                            ctx.Request.Path,
                            ctx.Exception.GetType().Name,
                            ctx.Exception.Message);
                        return Task.CompletedTask;
                    },
                    OnChallenge = ctx =>
                    {
                        ctx.HandleResponse();
                        ctx.Response.StatusCode = 401;
                        ctx.Response.ContentType = "application/json";
                        var correlationId = ctx.HttpContext.GetCorrelationId();
                        // Also log the challenge — fires when no Authentication scheme
                        // succeeded (e.g. missing Authorization header) so the auth
                        // failure event never fired.
                        var logger = ctx.HttpContext.RequestServices
                            .GetRequiredService<ILoggerFactory>()
                            .CreateLogger("Gateway.JwtBearer");
                        var authHeader = ctx.Request.Headers.Authorization.FirstOrDefault();
                        logger.LogWarning(
                            "JWT challenge for {Method} {Path}: error={Error} desc={Desc} authHeaderPresent={Present} authError={AuthFailure}",
                            ctx.Request.Method,
                            ctx.Request.Path,
                            ctx.Error,
                            ctx.ErrorDescription,
                            !string.IsNullOrEmpty(authHeader),
                            ctx.AuthenticateFailure?.Message);
                        return ctx.Response.WriteAsJsonAsync(DTOs.ErrorResponse.Unauthorized(correlationId));
                    }
                };
            });

        services.AddAuthorization();
        services.AddControllers();

        return services;
    }

    private static Uri BuildDownstreamUri(IConfiguration configuration, string settingsPath, string envName, string fallback)
    {
        var raw = configuration[settingsPath]
            ?? configuration[envName]
            ?? fallback;

        if (!raw.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            && !raw.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            raw = $"http://{raw}";
        }
        if (!raw.EndsWith('/'))
        {
            raw += "/";
        }
        return new Uri(raw, UriKind.Absolute);
    }

    private static Uri BuildAccountServiceUri(IConfiguration configuration)
    {
        var raw = configuration["ACCOUNT_SERVICE_URL"]
            ?? configuration["ACCOUNT_URL"]
            ?? "http://account-api:5000";

        if (!raw.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
            && !raw.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            raw = $"http://{raw}";
        }

        if (!raw.EndsWith('/'))
        {
            raw += "/";
        }

        return new Uri(raw, UriKind.Absolute);
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
