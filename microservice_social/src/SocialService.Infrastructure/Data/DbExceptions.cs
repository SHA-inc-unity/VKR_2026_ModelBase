using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace SocialService.Infrastructure.Data;

/// <summary>
/// Helpers for interpreting <see cref="DbUpdateException"/>s raised by Npgsql.
/// </summary>
internal static class DbExceptions
{
    // PostgreSQL SQLSTATE 23505 = unique_violation (covers primary-key and
    // unique-index conflicts). See https://www.postgresql.org/docs/current/errcodes-appendix.html
    private const string UniqueViolation = "23505";

    /// <summary>
    /// True when the failure is a primary-key / unique-constraint violation,
    /// i.e. a concurrent insert lost a race against an existing row. Such a
    /// conflict is safe to treat as an idempotent no-op.
    /// </summary>
    public static bool IsUniqueViolation(DbUpdateException ex) =>
        ex.InnerException is PostgresException { SqlState: UniqueViolation };
}
