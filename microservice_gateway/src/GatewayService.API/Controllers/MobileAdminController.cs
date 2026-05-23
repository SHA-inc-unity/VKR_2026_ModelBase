using System.Security.Claims;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Frontend;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

[ApiController]
[Route("api/admin")]
[Authorize(Roles = "admin")]
public sealed class MobileAdminController : ControllerBase
{
    private readonly IFrontendContractState _state;

    public MobileAdminController(IFrontendContractState state) => _state = state;

    [HttpGet("summary")]
    public ActionResult<MobileAdminSummaryResponse> GetSummary()
    {
        var snapshot = _state.GetAdminSnapshot();
        return Ok(new MobileAdminSummaryResponse
        {
            UsersCount = snapshot.UsersCount,
            LinkedExchangesCount = snapshot.LinkedExchangesCount,
            AlertsCount = snapshot.AlertsCount,
            EnabledServicesCount = CountEnabled(snapshot.ServiceToggles),
            GeneratedAt = DateTimeOffset.UtcNow,
        });
    }

    [HttpGet("users")]
    public ActionResult<IReadOnlyList<MobileAdminUserDto>> GetUsers()
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier)
            ?? User.FindFirstValue("sub");

        Guid.TryParse(userId, out var parsedId);
        var email = User.FindFirstValue(ClaimTypes.Email) ?? string.Empty;
        var username = User.FindFirstValue(ClaimTypes.Name) ?? email;
        var roles = User.FindAll(ClaimTypes.Role).Select(item => item.Value).ToArray();

        return Ok(new[]
        {
            new MobileAdminUserDto
            {
                Id = parsedId,
                Email = email,
                Username = username,
                Status = "active",
                Roles = roles,
            }
        });
    }

    [HttpGet("services")]
    public ActionResult<IReadOnlyList<MobileAdminServiceDto>> GetServices()
    {
        var toggles = _state.GetServiceToggles();
        return Ok(new[]
        {
            BuildService("news", toggles.News),
            BuildService("alerts", toggles.Alerts),
            BuildService("portfolioSync", toggles.PortfolioSync),
            BuildService("marketOverview", toggles.MarketOverview),
        });
    }

    [HttpGet("statistics")]
    public ActionResult<MobileAdminStatisticsResponse> GetStatistics()
    {
        var snapshot = _state.GetAdminSnapshot();
        return Ok(new MobileAdminStatisticsResponse
        {
            UsersCount = snapshot.UsersCount,
            LinkedExchangesCount = snapshot.LinkedExchangesCount,
            AlertsCount = snapshot.AlertsCount,
            AvailableExchangesCount = snapshot.AvailableExchangesCount,
            GeneratedAt = DateTimeOffset.UtcNow,
        });
    }

    private static MobileAdminServiceDto BuildService(string name, bool enabled) => new()
    {
        Name = name,
        Enabled = enabled,
        Status = enabled ? "enabled" : "disabled",
    };

    private static int CountEnabled(ServiceTogglesDto toggles)
    {
        var values = new[] { toggles.News, toggles.Alerts, toggles.PortfolioSync, toggles.MarketOverview };
        return values.Count(item => item);
    }
}