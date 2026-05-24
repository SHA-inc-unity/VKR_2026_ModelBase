namespace SocialService.Application.DTOs.Requests;

public sealed class CreateCommentRequest
{
    public string TargetType { get; set; } = string.Empty;
    public string TargetId { get; set; } = string.Empty;
    public string Body { get; set; } = string.Empty;
    public Guid? ParentId { get; set; }
}

public sealed class UpdateCommentRequest
{
    public string Body { get; set; } = string.Empty;
}
