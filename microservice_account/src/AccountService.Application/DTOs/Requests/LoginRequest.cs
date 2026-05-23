using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;

namespace AccountService.Application.DTOs.Requests;

public sealed record LoginRequest(
    [property: JsonPropertyName("email")] string? Email,
    [property: JsonPropertyName("login")] string? Login,
    [Required] string Password,
    string? DeviceId = null,
    string? DeviceName = null
)
{
    public LoginRequest(string emailOrLogin, string password)
        : this(Email: emailOrLogin, Login: null, Password: password)
    {
    }

    public string? Identifier =>
        !string.IsNullOrWhiteSpace(Login)
            ? Login
            : Email;
}
