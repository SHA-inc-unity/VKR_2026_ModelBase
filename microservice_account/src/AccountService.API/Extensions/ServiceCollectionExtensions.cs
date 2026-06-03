using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using System.Threading.RateLimiting;
using AccountService.API.Kafka;
using AccountService.Application.Common.Settings;
using AccountService.Application.Crypto;
using AccountService.Application.Interfaces.Cache;
using AccountService.Application.Interfaces.Repositories;
using AccountService.Application.Interfaces.Services;
using AccountService.Application.Services;
using AccountService.Application.Validators;
using AccountService.Infrastructure.Cache;
using AccountService.Infrastructure.Data;
using AccountService.Infrastructure.Repositories;
using FluentValidation;
using FluentValidation.AspNetCore;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Microsoft.OpenApi.Models;
using StackExchange.Redis;

namespace AccountService.API.Extensions;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddAccountServices(
        this IServiceCollection services,
        IConfiguration config)
    {
        // Settings
        services.Configure<JwtSettings>(config.GetSection(JwtSettings.SectionName));
        services.Configure<PasswordSettings>(config.GetSection(PasswordSettings.SectionName));
        services.Configure<KafkaSettings>(config.GetSection(KafkaSettings.SectionName));

        // Kafka
        services.AddSingleton<KafkaProducer>();
        services.AddHostedService<KafkaConsumerService>();

        // Database
        services.AddDbContext<AccountDbContext>(opt =>
            opt.UseNpgsql(
                   config.GetConnectionString("DefaultConnection"),
                   npgsql => npgsql.MigrationsAssembly(typeof(AccountDbContext).Assembly.GetName().Name))
               .UseSnakeCaseNamingConvention());

        // Redis (optional)
        var redisConnStr = config["Redis:ConnectionString"];
        if (!string.IsNullOrWhiteSpace(redisConnStr))
        {
            services.AddSingleton<IConnectionMultiplexer>(_ =>
                ConnectionMultiplexer.Connect(redisConnStr));
            services.AddScoped<ITokenCacheService, RedisTokenCacheService>();
        }
        else
        {
            services.AddScoped<ITokenCacheService, NullTokenCacheService>();
        }

        // Repositories
        services.AddScoped<IUserRepository, UserRepository>();
        services.AddScoped<IRoleRepository, RoleRepository>();
        services.AddScoped<IRefreshTokenRepository, RefreshTokenRepository>();
        services.AddScoped<IExchangeApiKeyRepository, ExchangeApiKeyRepository>();

        // Application services
        services.AddScoped<ITokenService, TokenService>();
        services.AddScoped<IPasswordService, PasswordService>();
        services.AddScoped<IAccountService, AccountAppService>();
        services.AddScoped<IExchangeApiKeyService, ExchangeApiKeyService>();

        // AES-GCM encryption singleton (master key from env / config).
        // Refuse to start on the built-in dev fallback key outside Development —
        // exchange API secrets would otherwise be encrypted with a key published
        // in the repo. (Live deployments pass ACCOUNT_API_KEY_MASTER_KEY via env.)
        var masterKey = config["ApiKeyEncryption:MasterKey"]
                        ?? Environment.GetEnvironmentVariable("ACCOUNT_API_KEY_MASTER_KEY");
        var isDevelopment = string.Equals(
            Environment.GetEnvironmentVariable("ASPNETCORE_ENVIRONMENT"),
            "Development", StringComparison.OrdinalIgnoreCase);
        if (string.IsNullOrWhiteSpace(masterKey) && !isDevelopment)
        {
            throw new InvalidOperationException(
                "ApiKeyEncryption:MasterKey (env ACCOUNT_API_KEY_MASTER_KEY) is required outside Development. " +
                "Refusing to start with the insecure built-in fallback key.");
        }
        services.AddSingleton<IAesGcmEncryption>(_ => new AesGcmEncryption(masterKey));

        // FluentValidation
        services.AddFluentValidationAutoValidation();
        services.AddValidatorsFromAssemblyContaining<RegisterRequestValidator>();

        // JWT Auth
        var jwtSection = config.GetSection(JwtSettings.SectionName);
        var key = Encoding.UTF8.GetBytes(jwtSection["SecretKey"] ?? string.Empty);

        services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
            .AddJwtBearer(opt =>
            {
                opt.TokenValidationParameters = new TokenValidationParameters
                {
                    ValidateIssuerSigningKey = true,
                    IssuerSigningKey = new SymmetricSecurityKey(key),
                    ValidateIssuer = true,
                    ValidIssuer = jwtSection["Issuer"],
                    ValidateAudience = true,
                    ValidAudience = jwtSection["Audience"],
                    ValidateLifetime = true,
                    ClockSkew = TimeSpan.FromSeconds(30)
                };

                // Consult the access-token revocation denylist (populated on
                // logout). No-op with NullTokenCacheService when Redis is off.
                opt.Events = new JwtBearerEvents
                {
                    OnTokenValidated = async context =>
                    {
                        var jti = context.Principal?.FindFirstValue(JwtRegisteredClaimNames.Jti);
                        if (string.IsNullOrEmpty(jti)) return;

                        var cache = context.HttpContext.RequestServices
                            .GetRequiredService<ITokenCacheService>();
                        if (await cache.IsAccessTokenRevokedAsync(jti, context.HttpContext.RequestAborted))
                            context.Fail("Access token has been revoked.");
                    }
                };
            });

        services.AddAuthorization();

        return services;
    }

    /// <summary>Policy name applied to the unauthenticated auth endpoints (login/refresh).</summary>
    public const string AuthRateLimitPolicy = "auth";

    /// <summary>
    /// Conservative brute-force throttle on login/refresh. Fixed window keyed by
    /// client IP with generous limits (60/min) so legitimate users are never
    /// affected; over-limit callers get HTTP 429.
    /// </summary>
    public static IServiceCollection AddAccountRateLimiting(this IServiceCollection services)
    {
        services.AddRateLimiter(options =>
        {
            options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;

            options.AddPolicy(AuthRateLimitPolicy, httpContext =>
            {
                var clientKey = httpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown";
                return RateLimitPartition.GetFixedWindowLimiter(clientKey, _ =>
                    new FixedWindowRateLimiterOptions
                    {
                        PermitLimit = 60,
                        Window = TimeSpan.FromMinutes(1),
                        QueueProcessingOrder = QueueProcessingOrder.OldestFirst,
                        QueueLimit = 0
                    });
            });
        });

        return services;
    }

    public static IServiceCollection AddAccountSwagger(this IServiceCollection services)
    {
        services.AddSwaggerGen(c =>
        {
            c.SwaggerDoc("v1", new OpenApiInfo
            {
                Title = "Account Service API",
                Version = "v1",
                Description = "Authentication & user management microservice"
            });

            c.AddSecurityDefinition("Bearer", new OpenApiSecurityScheme
            {
                Type = SecuritySchemeType.Http,
                Scheme = "bearer",
                BearerFormat = "JWT",
                Description = "Enter your JWT access token"
            });

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

    public static IServiceCollection AddAccountHealthChecks(
        this IServiceCollection services,
        IConfiguration config)
    {
        var hcBuilder = services.AddHealthChecks()
            .AddDbContextCheck<AccountDbContext>("database");

        var redisConnStr = config["Redis:ConnectionString"];
        if (!string.IsNullOrWhiteSpace(redisConnStr))
            hcBuilder.AddRedis(redisConnStr, "redis");

        return services;
    }
}
