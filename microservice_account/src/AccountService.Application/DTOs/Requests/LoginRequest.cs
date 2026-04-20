using System.ComponentModel.DataAnnotations;

namespace AccountService.Application.DTOs.Requests;

public sealed record LoginRequest(
    [Required, EmailAddress] string Email,
    [Required] string Password,
    string? DeviceId = null,
    string? DeviceName = null
);
