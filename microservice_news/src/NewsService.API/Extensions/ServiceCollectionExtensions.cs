using System.Net;
using System.Net.Sockets;
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
            // Send a real browser User-Agent + Accept-Language. Some news CDNs (Cloudflare in
            // front of Cointelegraph / CoinDesk / Decrypt) return 403/throttle responses to
            // plain bot agents, and only deliver the full RSS body when the request advertises
            // the gzip support and language headers a real browser would.
            c.DefaultRequestHeaders.UserAgent.ParseAdd(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
            c.DefaultRequestHeaders.Accept.ParseAdd(
                "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5");
            c.DefaultRequestHeaders.AcceptLanguage.ParseAdd("en-US,en;q=0.9");
            c.DefaultRequestHeaders.AcceptEncoding.ParseAdd("gzip, deflate, br");
        })
        .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
        {
            AllowAutoRedirect = true,
            MaxAutomaticRedirections = 5,
            AutomaticDecompression = DecompressionMethods.All,
            // Force IPv4 — the container's DNS often hands out AAAA records that have no working
            // outbound route in our docker network, which manifests as 30s read timeouts.
            ConnectCallback = static async (ctx, ct) =>
            {
                var endpoint = ctx.DnsEndPoint;
                var socket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp)
                {
                    NoDelay = true,
                };
                try
                {
                    await socket.ConnectAsync(endpoint.Host, endpoint.Port, ct);
                    return new NetworkStream(socket, ownsSocket: true);
                }
                catch
                {
                    socket.Dispose();
                    throw;
                }
            },
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
