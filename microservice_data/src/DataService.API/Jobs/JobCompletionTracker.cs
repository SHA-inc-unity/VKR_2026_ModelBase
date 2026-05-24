using System.Collections.Concurrent;

namespace DataService.API.Jobs;

/// <summary>
/// In-memory notifier that lets request-side code (e.g. the
/// <c>cmd.data.dataset.jobs.get</c> Kafka handler with a server-side wait
/// parameter) block until a specific job reaches a terminal state, without
/// resorting to a poll loop on <c>dataset_jobs</c>.
/// </summary>
/// <remarks>
/// <para>
/// The runner calls <see cref="Signal"/> from
/// <c>DatasetJobRunner.RunOneAsync</c>'s <c>finally</c> block after the row
/// has been transitioned to <c>succeeded</c> / <c>failed</c> / <c>canceled</c>
/// in the database, so any waiter that wakes up immediately reads a
/// consistent terminal record from <c>dataset_jobs</c>.
/// </para>
/// <para>
/// This is process-local. Gateways that talk to a multi-instance
/// microservice_data would still fall back to short-poll behaviour because
/// the waiter would simply time out and the handler would re-read the
/// current status from the DB. That's safe — completion is just an
/// optimization on top of the existing polling design.
/// </para>
/// </remarks>
public sealed class JobCompletionTracker
{
    private readonly ConcurrentDictionary<Guid, TaskCompletionSource<bool>> _waiters = new();

    /// <summary>
    /// Wait until <paramref name="jobId"/> is signaled as terminal, the
    /// timeout elapses, or the cancellation token fires. Returns true if a
    /// completion signal arrived; false on timeout / cancellation.
    /// </summary>
    public async Task<bool> WaitAsync(Guid jobId, TimeSpan timeout, CancellationToken ct)
    {
        if (timeout <= TimeSpan.Zero) return false;

        // RunContinuationsAsynchronously avoids running waiter continuations
        // inline on the runner thread that calls Signal.
        var tcs = _waiters.GetOrAdd(
            jobId,
            _ => new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously));

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        cts.CancelAfter(timeout);
        try
        {
            await using var reg = cts.Token.Register(static state =>
            {
                if (state is TaskCompletionSource<bool> t) t.TrySetResult(false);
            }, tcs);

            return await tcs.Task.ConfigureAwait(false);
        }
        finally
        {
            // Single-shot — drop the waiter so the next caller gets a fresh
            // TCS in case the same jobId is somehow re-used (it shouldn't be,
            // jobIds are GUIDs).
            _waiters.TryRemove(jobId, out _);
        }
    }

    /// <summary>
    /// Signal that <paramref name="jobId"/> has reached a terminal state.
    /// Idempotent and safe to call even if no one is waiting.
    /// </summary>
    public void Signal(Guid jobId)
    {
        if (_waiters.TryRemove(jobId, out var tcs))
        {
            tcs.TrySetResult(true);
        }
    }
}
