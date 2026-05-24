using System.Security.Claims;
using GatewayService.API.DTOs.Requests;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Frontend;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

[ApiController]
[Route("api/exchanges")]
[Authorize]
public sealed class ExchangesController : ControllerBase
{
    private readonly IFrontendContractState _state;

    public ExchangesController(IFrontendContractState state) => _state = state;

    [HttpGet("available")]
    public ActionResult<IReadOnlyList<AvailableExchangeDto>> GetAvailable() =>
        Ok(_state.GetAvailableExchanges(GetCurrentUserId()));

    [HttpGet("linked")]
    public ActionResult<IReadOnlyList<LinkedExchangeDto>> GetLinked() =>
        Ok(_state.GetLinkedExchanges(GetCurrentUserId()));

    [HttpPost("link")]
    public ActionResult<LinkedExchangeDto> Link([FromBody] LinkExchangeRequest request)
    {
        var linked = _state.LinkExchange(GetCurrentUserId(), request);
        return linked is null ? NotFound() : Ok(linked);
    }

    [HttpPatch("link/{slug}")]
    public ActionResult<LinkedExchangeDto> Update(string slug, [FromBody] UpdateExchangeLinkRequest request)
    {
        var linked = _state.UpdateExchange(GetCurrentUserId(), slug, request);
        return linked is null ? NotFound() : Ok(linked);
    }

    [HttpDelete("link/{slug}")]
    public IActionResult Delete(string slug)
    {
        return _state.DeleteExchange(GetCurrentUserId(), slug)
            ? NoContent()
            : NotFound();
    }

    private string GetCurrentUserId() =>
        User.FindFirstValue(ClaimTypes.NameIdentifier)
        ?? User.FindFirstValue("sub")
        ?? string.Empty;
}