namespace DataService.API.Markets;

public sealed record MarketWatchSymbol(string Symbol, string? RealtimeSymbol = null);

public interface IMarketDataClient
{
    string Exchange { get; }

    Task<IReadOnlyList<MarketWatchSymbol>> FetchMarketWatchSymbolsAsync(
        CancellationToken ct = default);

    Task<(long LaunchMs, long FundingMs)> FetchInstrumentDetailsAsync(
        string category,
        string symbol,
        CancellationToken ct = default);

    Task<IReadOnlyList<(long TimestampMs, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume, decimal Turnover)>>
        FetchKlinesAsync(
            string symbol,
            string interval,
            long startMs,
            long endMs,
            long stepMs,
            int maxParallel = 0,
            CancellationToken ct = default,
            Action<int, int>? onPageDone = null);

    Task<IReadOnlyList<(long TimestampMs, decimal Rate)>> FetchFundingRatesAsync(
        string symbol,
        long startMs,
        long endMs,
        long fundingIntervalMs = 28_800_000L,
        CancellationToken ct = default);

    Task<IReadOnlyList<(long TimestampMs, decimal Oi)>> FetchOpenInterestAsync(
        string symbol,
        string intervalLabel,
        long startMs,
        long endMs,
        long intervalMs,
        CancellationToken ct = default,
        Action<int, int>? onPageDone = null);
}