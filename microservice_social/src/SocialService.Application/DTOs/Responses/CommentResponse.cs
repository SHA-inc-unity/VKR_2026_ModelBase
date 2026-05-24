namespace SocialService.Application.DTOs.Responses;

public sealed class CommentAuthorDto
{
    public Guid Id { get; set; }
    public string Username { get; set; } = string.Empty;
}

public sealed class CommentResponse
{
    public Guid Id { get; set; }
    public CommentAuthorDto Author { get; set; } = new();
    public string TargetType { get; set; } = string.Empty;
    public string TargetId { get; set; } = string.Empty;
    public Guid? ParentId { get; set; }
    public string Body { get; set; } = string.Empty;
    public DateTime CreatedAt { get; set; }
    public DateTime UpdatedAt { get; set; }
    public bool Deleted { get; set; }
    public int LikeCount { get; set; }
    public bool LikedByMe { get; set; }
    public int ReplyCount { get; set; }
}

public sealed class CommentListResponse
{
    public IReadOnlyList<CommentResponse> Items { get; set; } = Array.Empty<CommentResponse>();
    public int Total { get; set; }
    public int Page { get; set; }
    public int PageSize { get; set; }
}

public sealed class FavoritesResponse
{
    public IReadOnlyList<string> Symbols { get; set; } = Array.Empty<string>();
}
