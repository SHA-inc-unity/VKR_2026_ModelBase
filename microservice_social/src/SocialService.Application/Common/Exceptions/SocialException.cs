namespace SocialService.Application.Common.Exceptions;

public abstract class SocialException : Exception
{
    public int StatusCode { get; }
    protected SocialException(string message, int statusCode) : base(message)
    {
        StatusCode = statusCode;
    }
}

public sealed class CommentNotFoundException : SocialException
{
    public CommentNotFoundException(Guid id) : base($"Comment {id} not found", 404) { }
}

public sealed class ForbiddenSocialActionException : SocialException
{
    public ForbiddenSocialActionException(string reason) : base(reason, 403) { }
}

public sealed class InvalidCommentTargetException : SocialException
{
    public InvalidCommentTargetException(string reason) : base(reason, 422) { }
}
