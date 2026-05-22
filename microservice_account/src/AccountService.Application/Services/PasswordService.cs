using AccountService.Application.Common.Exceptions;
using AccountService.Application.Common.Settings;
using AccountService.Application.Interfaces.Services;
using BCrypt.Net;
using Microsoft.Extensions.Options;

namespace AccountService.Application.Services;

public sealed class PasswordService : IPasswordService
{
    private readonly PasswordSettings _settings;

    public PasswordService(IOptions<PasswordSettings> settings)
    {
        _settings = settings.Value;
    }

    public string Hash(string password) =>
        BCrypt.Net.BCrypt.HashPassword(password, _settings.WorkFactor);

    public bool Verify(string password, string hash) =>
        BCrypt.Net.BCrypt.Verify(password, hash);

    public string? ValidateStrength(string password)
    {
        if (password.Length < _settings.MinLength)
            return $"minimum length is {_settings.MinLength} characters";

        if (_settings.RequireUppercase && !password.Any(char.IsUpper))
            return "must contain at least one uppercase letter";

        if (_settings.RequireLowercase && !password.Any(char.IsLower))
            return "must contain at least one lowercase letter";

        if (_settings.RequireDigit && !password.Any(char.IsDigit))
            return "must contain at least one digit";

        if (_settings.RequireSpecialChar && !password.Any(c => !char.IsLetterOrDigit(c)))
            return "must contain at least one special character";

        return null;
    }
}
