using System.Globalization;
using System.Text.Json;
using NotificationService.Application.Interfaces;

namespace NotificationService.API.Services;

public sealed class HttpMarketSnapshotService : IMarketSnapshotService
{
    private readonly HttpClient _client;
    private readonly ILogger<HttpMarketSnapshotService> _log;

    public HttpMarketSnapshotService(HttpClient client, ILogger<HttpMarketSnapshotService> log)
    {
        _client = client;
        _log = log;
    }

    public async Task<IReadOnlyDictionary<string, decimal>> GetSnapshotAsync(IEnumerable<string> symbols, CancellationToken ct)
    {
        var result = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
        var distinct = symbols.Where(s => !string.IsNullOrWhiteSpace(s))
                              .Select(s => s.Trim().ToUpperInvariant())
                              .Distinct()
                              .ToArray();
        if (distinct.Length == 0) return result;

        try
        {
            var qs = "symbols=" + string.Join(',', distinct);
            // The gateway has no /snapshot route; quotes/realtime is the live per-symbol
            // price endpoint (source "market-watch-live"). Response is { items: [{symbol,
            // price}], missingSymbols, meta } — handled by the parser below. Fixing the
            // path here also un-breaks the favorite price-drift watcher, which shares this service.
            using var resp = await _client.GetAsync($"/api/v1/market/quotes/realtime?{qs}", ct);
            if (!resp.IsSuccessStatusCode)
            {
                _log.LogDebug("Market snapshot returned {Status}", (int)resp.StatusCode);
                return result;
            }
            var body = await resp.Content.ReadAsStringAsync(ct);
            using var doc = JsonDocument.Parse(body);

            // Two possible shapes — array of {symbol, price} OR { items: [...] }
            JsonElement items;
            if (doc.RootElement.ValueKind == JsonValueKind.Array)
            {
                items = doc.RootElement;
            }
            else if (doc.RootElement.TryGetProperty("items", out var it))
            {
                items = it;
            }
            else
            {
                return result;
            }

            foreach (var it in items.EnumerateArray())
            {
                string? sym = null;
                if (it.TryGetProperty("symbol", out var s) && s.ValueKind == JsonValueKind.String)
                    sym = s.GetString();

                decimal? price = null;
                if (it.TryGetProperty("price", out var p))
                {
                    if (p.ValueKind == JsonValueKind.Number && p.TryGetDecimal(out var d)) price = d;
                    else if (p.ValueKind == JsonValueKind.String &&
                             decimal.TryParse(p.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var d2)) price = d2;
                }

                if (sym is not null && price is not null)
                {
                    result[sym.ToUpperInvariant()] = price.Value;
                }
            }
        }
        catch (Exception ex)
        {
            _log.LogDebug(ex, "Market snapshot fetch failed");
        }

        return result;
    }
}
