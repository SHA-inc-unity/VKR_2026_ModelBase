using System.ComponentModel.DataAnnotations;

namespace AccountService.Application.DTOs.Requests;

public sealed record UpdateProfileRequest(
    [MinLength(3), MaxLength(50)] string? Username = null
);
