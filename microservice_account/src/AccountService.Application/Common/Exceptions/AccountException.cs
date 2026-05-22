namespace AccountService.Application.Common.Exceptions;

public abstract class AccountException : Exception
{
    protected AccountException(string message) : base(message) { }
    protected AccountException(string message, Exception inner) : base(message, inner) { }
}
