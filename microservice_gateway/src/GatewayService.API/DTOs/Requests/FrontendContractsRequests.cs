using System.ComponentModel.DataAnnotations;

namespace GatewayService.API.DTOs.Requests;

public sealed record LinkExchangeRequest(
    [Required] string Slug,
    [Required] string ApiKey,
    string? ApiSecret = null);

public sealed record UpdateExchangeLinkRequest(
    string? ApiKey = null,
    string? ApiSecret = null,
    bool? IsActive = null);

public sealed record CreateAlertRequest(
    [Required] string Symbol,
    [Required] string Condition,
    decimal TargetPrice,
    bool IsEnabled = true);

public sealed record UpdateAlertRequest(
    string? Symbol = null,
    string? Condition = null,
    decimal? TargetPrice = null,
    bool? IsEnabled = null);

public sealed record PatchServiceTogglesRequest(
    bool? News = null,
    bool? Alerts = null,
    bool? PortfolioSync = null,
    bool? MarketOverview = null);

public sealed record BatchMarketQuotesRequest(
    [Required] IReadOnlyList<string> Symbols);