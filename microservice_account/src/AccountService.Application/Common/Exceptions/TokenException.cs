namespace AccountService.Application.Common.Exceptions;

public sealed class TokenException : AccountException
{
    public TokenException(string message) : base(message) { }

    public static TokenException Expired() => new("Refresh token has expired.");
    public static TokenException Revoked() => new("Refresh token has been revoked.");
    public static TokenException Invalid() => new("Refresh token is invalid.");
}
