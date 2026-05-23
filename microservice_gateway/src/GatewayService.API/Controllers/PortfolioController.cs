using System.Security.Claims;
using GatewayService.API.Clients.Portfolio;
using GatewayService.API.DTOs;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace GatewayService.API.Controllers;

[ApiController]
[Route("api/portfolio")]
[Authorize]
public sealed class PortfolioController : ControllerBase
{
    private readonly IPortfolioServiceClient _portfolio;

    public PortfolioController(IPortfolioServiceClient portfolio) => _portfolio = portfolio;

    [HttpGet("summary")]
    public async Task<IActionResult> GetSummary(CancellationToken ct)
    {
        var result = await _portfolio.GetDetailedSummaryAsync(GetCurrentUserId(), ct);
        return result.IsSuccess
            ? Ok(result.Value)
            : StatusCode(503, ErrorResponse.ServiceUnavailable("portfolio"));
    }

    private string GetCurrentUserId() =>
        User.FindFirstValue(ClaimTypes.NameIdentifier)
        ?? User.FindFirstValue("sub")
        ?? string.Empty;
}