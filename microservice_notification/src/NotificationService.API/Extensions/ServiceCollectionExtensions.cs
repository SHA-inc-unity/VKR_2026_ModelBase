using System.Text;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Microsoft.OpenApi.Models;
using NotificationService.API.BackgroundJobs;
using NotificationService.API.Kafka;
using NotificationService.API.Services;
using NotificationService.API.Sse;
using NotificationService.Application.Common.Settings;
using NotificationService.Application.Interfaces;
using NotificationService.Application.Services;
using NotificationService.Infrastructure.Data;
using NotificationService.Infrastructure.PushNotifications;
using NotificationService.Infrastructure.Repositories;

namespace NotificationService.API.Extensions;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddNotificationServices(
        this IServiceCollection services,
        IConfiguration config)
    {
        services.Configure<JwtSettings>(config.GetSection(JwtSettings.SectionName));
        services.Configure<NotificationKafkaSettings>(config.GetSection(NotificationKafkaSettings.SectionName));
        services.Configure<SocialServiceSettings>(config.GetSection(SocialServiceSettings.SectionName));
        services.Configure<GatewaySettings>(config.GetSection(GatewaySettings.SectionName));
        services.Configure<PriceWatcherSettings>(config.GetSection(PriceWatcherSettings.SectionName));
        services.Configure<PushSettings>(config.GetSection(PushSettings.SectionName));

        services.AddDbContext<NotificationDbContext>(opt =>
            opt.UseNpgsql(
                   config.GetConnectionString("DefaultConnection"),
                   npgsql => npgsql.MigrationsAssembly(typeof(NotificationDbContext).Assembly.GetName().Name))
               .UseSnakeCaseNamingConvention());

        services.AddScoped<INotificationRepository, NotificationRepository>();
        services.AddScoped<INotificationSettingsRepository, NotificationSettingsRepository>();
        services.AddScoped<IPushSubscriptionRepository, PushSubscriptionRepository>();
        services.AddScoped<IWebPushSender, WebPushSender>();
        services.AddScoped<INotificationsAppService, NotificationsAppService>();

        services.AddSingleton<SseDispatcher>();
        services.AddSingleton<ISseDispatcher>(sp => sp.GetRequiredService<SseDispatcher>());
        services.AddSingleton<IJwtTokenValidator, JwtTokenValidator>();

        services.AddHttpClient<ISocialDirectoryService, HttpSocialDirectoryService>((sp, c) =>
        {
            var opts = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<SocialServiceSettings>>().Value;
            c.BaseAddress = new Uri(opts.BaseUrl);
            c.Timeout = TimeSpan.FromSeconds(5);
            if (!string.IsNullOrEmpty(opts.InternalApiKey))
                c.DefaultRequestHeaders.Add("X-Internal-Api-Key", opts.InternalApiKey);
        });

        services.AddHttpClient<IMarketSnapshotService, HttpMarketSnapshotService>((sp, c) =>
        {
            var opts = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<GatewaySettings>>().Value;
            c.BaseAddress = new Uri(opts.BaseUrl);
            c.Timeout = TimeSpan.FromSeconds(10);
        });

        services.AddHostedService<KafkaConsumerService>();
        services.AddHostedService<PriceDriftWatcherService>();

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
                    ClockSkew = TimeSpan.FromSeconds(30),
                };
            });

        services.AddAuthorization();

        return services;
    }

    public static IServiceCollection AddNotificationSwagger(this IServiceCollection services)
    {
        services.AddSwaggerGen(c =>
        {
            c.SwaggerDoc("v1", new OpenApiInfo
            {
                Title = "Notification Service API",
                Version = "v1",
                Description = "Inbox, SSE delivery, per-user settings",
            });
            c.AddSecurityDefinition("Bearer", new OpenApiSecurityScheme
            {
                Type = SecuritySchemeType.Http,
                Scheme = "bearer",
                BearerFormat = "JWT",
            });
            c.AddSecurityRequirement(new OpenApiSecurityRequirement
            {
                {
                    new OpenApiSecurityScheme
                    {
                        Reference = new OpenApiReference
                        {
                            Type = ReferenceType.SecurityScheme,
                            Id = "Bearer",
                        },
                    },
                    Array.Empty<string>()
                },
            });
        });
        return services;
    }

    public static IServiceCollection AddNotificationHealthChecks(
        this IServiceCollection services,
        IConfiguration _)
    {
        services.AddHealthChecks().AddDbContextCheck<NotificationDbContext>("database");
        return services;
    }
}
