namespace AccountService.Application.Common.Exceptions;

public sealed class InvalidCredentialsException : AccountException
{
    public InvalidCredentialsException()
        : base("Invalid email or password.") { }
}
