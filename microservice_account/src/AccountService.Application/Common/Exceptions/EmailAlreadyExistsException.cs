namespace AccountService.Application.Common.Exceptions;

public sealed class EmailAlreadyExistsException : AccountException
{
    public EmailAlreadyExistsException(string email)
        : base($"Email '{email}' is already registered.") { }
}
