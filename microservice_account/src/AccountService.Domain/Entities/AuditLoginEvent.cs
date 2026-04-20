using AccountService.Domain.Enums;

namespace AccountService.Domain.Entities;

public class AuditLoginEvent
{
    public Guid Id { get; private set; }
    public Guid UserId { get; private set; }
    public AuditEventType EventType { get; private set; }
    public string? IpAddress { get; private set; }
    public string? UserAgent { get; private set; }
    public string? Metadata { get; private set; }
    public DateTimeOffset OccurredAt { get; private set; }

    public User User { get; private set; } = null!;

    private AuditLoginEvent() { }

    public static AuditLoginEvent Create(
        Guid userId,
        AuditEventType eventType,
        string? ipAddress = null,
        string? userAgent = null,
        string? metadata = null) =>
        new()
        {
            Id = Guid.NewGuid(),
            UserId = userId,
            EventType = eventType,
            IpAddress = ipAddress,
            UserAgent = userAgent,
            Metadata = metadata,
            OccurredAt = DateTimeOffset.UtcNow
        };
}
