using System.Text;
using FluentValidation;
using FluentValidation.AspNetCore;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Microsoft.OpenApi.Models;
using SocialService.API.Kafka;
using SocialService.API.Services;
using SocialService.Application.Common.Settings;
using SocialService.Application.Interfaces.Repositories;
using SocialService.Application.Interfaces.Services;
using SocialService.Application.Services;
using SocialService.Application.Validators;
using SocialService.Infrastructure.Data;
using SocialService.Infrastructure.Repositories;

namespace SocialService.API.Extensions;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddSocialServices(
        this IServiceCollection services,
        IConfiguration config)
    {
        services.Configure<JwtSettings>(config.GetSection(JwtSettings.SectionName));
        services.Configure<KafkaSettings>(config.GetSection(KafkaSettings.SectionName));
        services.Configure<AccountServiceSettings>(config.GetSection(AccountServiceSettings.SectionName));

        services.AddSingleton<IEventBus, KafkaEventBus>();

        services.AddDbContext<SocialDbContext>(opt =>
            opt.UseNpgsql(
                   config.GetConnectionString("DefaultConnection"),
                   npgsql => npgsql.MigrationsAssembly(typeof(SocialDbContext).Assembly.GetName().Name))
               .UseSnakeCaseNamingConvention());

        services.AddScoped<IFavoriteRepository, FavoriteRepository>();
        services.AddScoped<ICommentRepository, CommentRepository>();
        services.AddScoped<ICommentLikeRepository, CommentLikeRepository>();

        services.AddScoped<IFavoritesAppService, FavoritesAppService>();
        services.AddScoped<ICommentsAppService, CommentsAppService>();

        services.AddHttpClient<IUserDirectoryService, HttpUserDirectoryService>((sp, c) =>
        {
            var opts = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<AccountServiceSettings>>().Value;
            c.BaseAddress = new Uri(opts.BaseUrl);
            c.Timeout = TimeSpan.FromSeconds(5);
        });

        services.AddFluentValidationAutoValidation();
        services.AddValidatorsFromAssemblyContaining<CreateCommentRequestValidator>();

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

    public static IServiceCollection AddSocialSwagger(this IServiceCollection services)
    {
        services.AddSwaggerGen(c =>
        {
            c.SwaggerDoc("v1", new OpenApiInfo
            {
                Title = "Social Service API",
                Version = "v1",
                Description = "Favorites, comments, likes and replies",
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

    public static IServiceCollection AddSocialHealthChecks(
        this IServiceCollection services,
        IConfiguration _)
    {
        services.AddHealthChecks().AddDbContextCheck<SocialDbContext>("database");
        return services;
    }
}
