using GatewayService.API.DTOs.Requests;
using GatewayService.API.DTOs.Responses;

namespace GatewayService.API.Frontend;

public interface IFrontendContractState
{
    PortfolioSummaryDto GetDashboardPortfolioSummary(string userId);
    PortfolioDetailedSummaryResponse GetPortfolioSummary(string userId);
    IReadOnlyList<AvailableExchangeDto> GetAvailableExchanges(string userId);
    IReadOnlyList<LinkedExchangeDto> GetLinkedExchanges(string userId);
    LinkedExchangeDto? LinkExchange(string userId, LinkExchangeRequest request);
    LinkedExchangeDto? UpdateExchange(string userId, string slug, UpdateExchangeLinkRequest request);
    bool DeleteExchange(string userId, string slug);
    IReadOnlyList<PriceAlertDto> GetAlerts(string userId);
    PriceAlertDto CreateAlert(string userId, CreateAlertRequest request);
    PriceAlertDto? UpdateAlert(string userId, string id, UpdateAlertRequest request);
    bool DeleteAlert(string userId, string id);
    ServiceTogglesDto GetServiceToggles();
    ServiceTogglesDto UpdateServiceToggles(PatchServiceTogglesRequest request);
    FrontendAdminSnapshot GetAdminSnapshot();
}

public sealed record FrontendAdminSnapshot(
    int UsersCount,
    int LinkedExchangesCount,
    int AlertsCount,
    int AvailableExchangesCount,
    ServiceTogglesDto ServiceToggles);