namespace AccountService.Application.Common.Exceptions;

public sealed class UsernameAlreadyExistsException : AccountException
{
    public UsernameAlreadyExistsException(string username)
        : base($"Username '{username}' is already taken.") { }
}
