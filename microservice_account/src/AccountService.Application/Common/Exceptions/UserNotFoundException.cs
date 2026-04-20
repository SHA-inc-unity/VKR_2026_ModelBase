namespace AccountService.Application.Common.Exceptions;

public sealed class UserNotFoundException : AccountException
{
    public UserNotFoundException(Guid userId)
        : base($"User '{userId}' was not found.") { }

    public UserNotFoundException(string email)
        : base($"User with email '{email}' was not found.") { }
}
