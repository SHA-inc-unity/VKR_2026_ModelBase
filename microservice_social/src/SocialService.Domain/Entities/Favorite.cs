namespace SocialService.Domain.Entities;

public sealed class Favorite
{
    public Guid UserId { get; private set; }
    public string Symbol { get; private set; } = string.Empty;
    public DateTime CreatedAt { get; private set; }

    private Favorite() { }

    public static Favorite Create(Guid userId, string symbol)
    {
        return new Favorite
        {
            UserId = userId,
            Symbol = symbol.Trim().ToUpperInvariant(),
            CreatedAt = DateTime.UtcNow,
        };
    }
}
