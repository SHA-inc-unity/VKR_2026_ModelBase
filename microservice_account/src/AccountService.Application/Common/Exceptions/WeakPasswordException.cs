namespace AccountService.Application.Common.Exceptions;

public sealed class WeakPasswordException : AccountException
{
    public WeakPasswordException(string reason)
        : base($"Password does not meet requirements: {reason}") { }
}
