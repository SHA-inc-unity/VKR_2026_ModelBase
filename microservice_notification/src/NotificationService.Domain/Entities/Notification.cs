namespace NotificationService.Domain.Entities;

public sealed class Notification
{
    public Guid Id { get; private set; }
    public Guid UserId { get; private set; }
    public string Kind { get; private set; } = string.Empty;
    public string Title { get; private set; } = string.Empty;
    public string Body { get; private set; } = string.Empty;
    public string? Deeplink { get; private set; }
    public string? PayloadJson { get; private set; }
    public string? DedupKey { get; private set; }
    public DateTime CreatedAt { get; private set; }
    public DateTime? ReadAt { get; private set; }

    private Notification() { }

    public static Notification Create(
        Guid userId,
        string kind,
        string title,
        string body,
        string? deeplink,
        string? payloadJson,
        string? dedupKey = null)
    {
        return new Notification
        {
            Id = Guid.NewGuid(),
            UserId = userId,
            Kind = kind,
            Title = title,
            Body = body,
            Deeplink = deeplink,
            PayloadJson = payloadJson,
            DedupKey = dedupKey,
            CreatedAt = DateTime.UtcNow,
            ReadAt = null,
        };
    }

    public void MarkRead()
    {
        if (ReadAt is null) ReadAt = DateTime.UtcNow;
    }
}
