using DataService.API.Settings;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;

namespace DataService.API.Controllers;

[ApiController]
[Route("")]
public sealed class HealthController : ControllerBase
{
    private readonly DataServiceSettings _settings;

    public HealthController(IOptions<DataServiceSettings> opts) =>
        _settings = opts.Value;

    [HttpGet("health")]
    public IActionResult GetHealth() => Ok(new
    {
        status = "ok",
        service = _settings.ServiceName,
        version = _settings.Version,
    });
}
