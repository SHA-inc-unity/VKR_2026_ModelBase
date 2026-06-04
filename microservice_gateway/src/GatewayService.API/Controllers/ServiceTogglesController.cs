using GatewayService.API.DTOs.Requests;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Frontend;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

[ApiController]
[Route("api/services")]
[Authorize]
public sealed class ServiceTogglesController : ControllerBase
{
    private readonly IFrontendContractState _state;

    public ServiceTogglesController(IFrontendContractState state) => _state = state;

    [HttpGet("toggles")]
    public ActionResult<ServiceTogglesDto> GetToggles() =>
        Ok(_state.GetServiceToggles());

    // Writing global service toggles affects every user (News/Alerts/
    // PortfolioSync/MarketOverview), so it must be admin-only. The GET above
    // stays open to any authenticated user (clients read it as feature flags).
    [HttpPatch("toggles")]
    [Authorize(Roles = "admin")]
    public ActionResult<ServiceTogglesDto> UpdateToggles([FromBody] PatchServiceTogglesRequest request) =>
        Ok(_state.UpdateServiceToggles(request));
}