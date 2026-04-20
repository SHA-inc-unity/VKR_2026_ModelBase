using System.Text;
using AccountService.Application.Common.Settings;
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

        // Database
        services.AddDbContext<AccountDbContext>(opt =>
            opt.UseNpgsql(config.GetConnectionString("DefaultConnection"))
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

        // Application services
        services.AddScoped<ITokenService, TokenService>();
        services.AddScoped<IPasswordService, PasswordService>();
        services.AddScoped<IAccountService, AccountAppService>();

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
            });

        services.AddAuthorization();

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
