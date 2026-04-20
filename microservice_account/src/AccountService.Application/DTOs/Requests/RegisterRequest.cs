using System.ComponentModel.DataAnnotations;

namespace AccountService.Application.DTOs.Requests;

public sealed record RegisterRequest(
    [Required, EmailAddress, MaxLength(320)] string Email,
    [Required, MinLength(3), MaxLength(50)] string Username,
    [Required, MinLength(8), MaxLength(128)] string Password
);
