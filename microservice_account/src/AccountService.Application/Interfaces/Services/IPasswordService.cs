namespace AccountService.Application.Interfaces.Services;

public interface IPasswordService
{
    string Hash(string password);
    bool Verify(string password, string hash);
    /// <summary>Validates password strength. Returns null if valid, error message if invalid.</summary>
    string? ValidateStrength(string password);
}
