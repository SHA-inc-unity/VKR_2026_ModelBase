using System.ComponentModel.DataAnnotations;

namespace AccountService.Application.DTOs.Requests;

public sealed record RefreshTokenRequest(
    [Required] string RefreshToken
);
