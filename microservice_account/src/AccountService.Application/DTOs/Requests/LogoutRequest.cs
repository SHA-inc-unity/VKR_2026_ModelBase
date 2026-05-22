using System.ComponentModel.DataAnnotations;

namespace AccountService.Application.DTOs.Requests;

public sealed record LogoutRequest(
    [Required] string RefreshToken
);
