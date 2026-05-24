using System.Threading.Channels;
using DataService.API.Database;

namespace DataService.API.Jobs;

/// <summary>
/// In-memory push-notify channel between the request-side (KafkaConsumer
/// HandleJobsStartAsync) and the worker-side (DatasetJobRunner). Replaces
/// the old DB-polling loop so a queued job is picked up in well under a
/// millisecond after the INSERT commits.
///
/// The channel only carries a hint (the freshly inserted DatasetJobRecord).
/// DatasetJobRunner re-reads / re-acquires the row from the DB before
/// running it so the channel is never the source of truth — orphan
/// reclaim, dedup, and cancel still flow through the existing DB columns.
/// </summary>
public sealed class JobDispatchChannel
{
    private readonly Channel<DatasetJobRecord> _channel;

    public JobDispatchChannel()
    {
        // Unbounded so the request path never blocks writing into the
        // channel — the worst case is the runner falling behind, which
        // already had a polling safety-net for that scenario.
        _channel = Channel.CreateUnbounded<DatasetJobRecord>(
            new UnboundedChannelOptions
            {
                SingleReader = true,
                SingleWriter = false,
                AllowSynchronousContinuations = false,
            });
    }

    /// <summary>
    /// Notify the runner about a newly queued job. Best-effort — if the
    /// channel happens to be closed (during shutdown) the runner will
    /// still pick up the row on its next safety-net poll cycle.
    /// </summary>
    public void Publish(DatasetJobRecord job)
    {
        _channel.Writer.TryWrite(job);
    }

    public ChannelReader<DatasetJobRecord> Reader => _channel.Reader;
}
