using Microsoft.EntityFrameworkCore;
using Microsoft.OpenApi.Models;
using NewsService.API.BackgroundJobs;
using NewsService.API.Kafka;
using NewsService.Application.Common.Settings;
using NewsService.Application.Interfaces;
using NewsService.Application.Services;
using NewsService.Infrastructure.Data;
using NewsService.Infrastructure.Repositories;

namespace NewsService.API.Extensions;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddNewsServices(
        this IServiceCollection services,
        IConfiguration config)
    {
        services.Configure<NewsKafkaSettings>(config.GetSection(NewsKafkaSettings.SectionName));
        services.Configure<CryptoPanicSettings>(config.GetSection(CryptoPanicSettings.SectionName));

        services.AddSingleton<INewsEventBus, KafkaNewsEventBus>();

        services.AddDbContext<NewsDbContext>(opt =>
            opt.UseNpgsql(
                   config.GetConnectionString("DefaultConnection"),
                   npgsql => npgsql.MigrationsAssembly(typeof(NewsDbContext).Assembly.GetName().Name))
               .UseSnakeCaseNamingConvention());

        services.AddScoped<INewsRepository, NewsRepository>();
        services.AddScoped<INewsAppService, NewsAppService>();

        services.AddHttpClient("cryptopanic", c =>
        {
            c.Timeout = TimeSpan.FromSeconds(30);
            c.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0 (compatible; ModelLine-NewsService/1.0; +https://modelline.app)");
            c.DefaultRequestHeaders.Accept.ParseAdd("application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5");
        })
        .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
        {
            AllowAutoRedirect = true,
            MaxAutomaticRedirections = 5,
        });

        services.AddHostedService<CryptoPanicIngesterService>();

        return services;
    }

    public static IServiceCollection AddNewsSwagger(this IServiceCollection services)
    {
        services.AddSwaggerGen(c =>
        {
            c.SwaggerDoc("v1", new OpenApiInfo
            {
                Title = "News Service API",
                Version = "v1",
                Description = "CryptoPanic-backed news feed with DB cache",
            });
        });
        return services;
    }

    public static IServiceCollection AddNewsHealthChecks(
        this IServiceCollection services,
        IConfiguration _)
    {
        services.AddHealthChecks().AddDbContextCheck<NewsDbContext>("database");
        return services;
    }
}
