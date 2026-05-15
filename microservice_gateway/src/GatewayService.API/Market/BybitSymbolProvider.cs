using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Market;

/// <summary>
/// Fetches active linear USDT perpetual symbols from Bybit's
/// <c>/v5/market/instruments-info?category=linear</c> endpoint.
///
/// Only instruments that satisfy all of the following are included:
/// - status = "Trading"
/// - quoteCoin = "USDT"
/// - contractType = "LinearPerpetual"
///
/// The result is sorted alphabetically so the client always sees a stable list.
/// All exceptions are swallowed and an empty list is returned so that a Bybit
/// outage never breaks the gateway.
/// </summary>
public sealed class BybitSymbolProvider : IBybitSymbolProvider
{
    // Hardcoded fallback used when Bybit API is unreachable and the Redis cache is empty.
    // Covers the most liquid USDT perpetuals so the API remains usable during outages.
    private static readonly IReadOnlyList<string> FallbackSymbols =
    [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "DOGEUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "LTCUSDT", "BCHUSDT", "NEARUSDT", "FILUSDT",
        "ATOMUSDT", "UNIUSDT", "AAVEUSDT", "APTUSDT", "OPUSDT",
    ];

    private readonly IHttpClientFactory _httpClientFactory;
    private readonly MarketSettings _settings;
    private readonly ILogger<BybitSymbolProvider> _log;

    public BybitSymbolProvider(
        IHttpClientFactory httpClientFactory,
        IOptions<MarketSettings> settings,
        ILogger<BybitSymbolProvider> log)
    {
        _httpClientFactory = httpClientFactory;
        _settings = settings.Value;
        _log = log;
    }

    public async Task<IReadOnlyList<string>> GetActiveSymbolsAsync(CancellationToken ct = default)
    {
        // Bybit paginates at up to 1000 per page; linear USDT perp universe
        // is several hundred instruments, well within a single page.
        var url = $"{_settings.BybitBaseUrl}/v5/market/instruments-info?category=linear&limit=1000";

        try
        {
            using var http = _httpClientFactory.CreateClient(nameof(BybitSymbolProvider));
            using var response = await http.GetAsync(url, ct);
            response.EnsureSuccessStatusCode();

            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var doc = await JsonDocument.ParseAsync(stream, cancellationToken: ct);

            if (!doc.RootElement.TryGetProperty("retCode", out var retCodeEl) ||
                retCodeEl.GetInt32() != 0)
            {
                _log.LogWarning("Bybit instruments-info returned non-zero retCode");
                return FallbackSymbols;
            }

            if (!doc.RootElement.TryGetProperty("result", out var resultEl) ||
                !resultEl.TryGetProperty("list", out var listEl))
            {
                _log.LogWarning("Bybit instruments-info response missing result.list");
                return FallbackSymbols;
            }

            var symbols = new List<string>();
            foreach (var item in listEl.EnumerateArray())
            {
                if (!item.TryGetProperty("status", out var statusEl) ||
                    statusEl.GetString() != "Trading")
                    continue;

                if (!item.TryGetProperty("quoteCoin", out var quoteEl) ||
                    quoteEl.GetString() != "USDT")
                    continue;

                if (!item.TryGetProperty("contractType", out var contractEl) ||
                    contractEl.GetString() != "LinearPerpetual")
                    continue;

                var sym = item.TryGetProperty("symbol", out var symEl)
                    ? symEl.GetString()
                    : null;
                if (!string.IsNullOrEmpty(sym))
                    symbols.Add(sym);
            }

            if (symbols.Count == 0)
            {
                _log.LogWarning("Bybit returned an empty active symbol list; using fallback");
                return FallbackSymbols;
            }

            symbols.Sort(StringComparer.Ordinal);
            _log.LogInformation("Bybit symbol provider: fetched {Count} active symbols", symbols.Count);
            return symbols;
        }
        catch (Exception ex)
        {
            _log.LogError(ex, "Failed to fetch symbols from Bybit; using fallback list");
            return FallbackSymbols;
        }
    }

    /// <summary>Exposed for tests — returns the built-in fallback list.</summary>
    internal static IReadOnlyList<string> GetFallback() => FallbackSymbols;
}
