using System.Security.Claims;
using GatewayService.API.DTOs.Requests;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Frontend;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

[ApiController]
[Route("api/alerts")]
[Authorize]
public sealed class AlertsController : ControllerBase
{
    private readonly IFrontendContractState _state;

    public AlertsController(IFrontendContractState state) => _state = state;

    [HttpGet]
    public ActionResult<IReadOnlyList<PriceAlertDto>> GetAlerts() =>
        Ok(_state.GetAlerts(GetCurrentUserId()));

    [HttpPost]
    public ActionResult<PriceAlertDto> Create([FromBody] CreateAlertRequest request) =>
        Ok(_state.CreateAlert(GetCurrentUserId(), request));

    [HttpPatch("{id}")]
    public ActionResult<PriceAlertDto> Update(string id, [FromBody] UpdateAlertRequest request)
    {
        var alert = _state.UpdateAlert(GetCurrentUserId(), id, request);
        return alert is null ? NotFound() : Ok(alert);
    }

    [HttpDelete("{id}")]
    public IActionResult Delete(string id)
    {
        return _state.DeleteAlert(GetCurrentUserId(), id)
            ? NoContent()
            : NotFound();
    }

    private string GetCurrentUserId() =>
        User.FindFirstValue(ClaimTypes.NameIdentifier)
        ?? User.FindFirstValue("sub")
        ?? string.Empty;
}