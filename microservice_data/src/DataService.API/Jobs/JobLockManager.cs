using System.Collections.Concurrent;

namespace DataService.API.Jobs;

/// <summary>
/// In-process lock for the (target_table, conflict_class) tuple. Two
/// active jobs holding the same key cannot run simultaneously. Jobs whose
/// target_table is null share a single global key per conflict_class.
///
/// This is a process-local guard. The DatasetJobRunner is a single
/// BackgroundService inside microservice_data, so the in-memory map is
/// authoritative; multi-instance deployments would need a Postgres
/// advisory lock layer on top (out of scope for Phase B).
/// </summary>
public sealed class JobLockManager
{
    private readonly ConcurrentDictionary<string, byte> _locks = new(StringComparer.Ordinal);

    private static string Key(string? table, string conflictClass) =>
        $"{conflictClass}::{table ?? "*"}";

    public bool TryAcquire(string? table, string conflictClass) =>
        _locks.TryAdd(Key(table, conflictClass), 0);

    public void Release(string? table, string conflictClass) =>
        _locks.TryRemove(Key(table, conflictClass), out _);

    public bool IsHeld(string? table, string conflictClass) =>
        _locks.ContainsKey(Key(table, conflictClass));
}
