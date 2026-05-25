using System.Globalization;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace GatewayService.API.Clients.Bybit;

public interface IBybitPrivateClient
{
    Task<BybitWalletBalance> GetUnifiedWalletAsync(
        string apiKey,
        string apiSecret,
        CancellationToken ct = default);
}

public sealed class BybitApiException : Exception
{
    public int RetCode { get; }
    public string RetMsg { get; }
    public BybitApiException(int retCode, string retMsg)
        : base($"Bybit retCode={retCode}: {retMsg}")
    {
        RetCode = retCode;
        RetMsg = retMsg;
    }
}

/// <summary>
/// Coin-level balance row inside a Bybit V5 wallet snapshot.
/// </summary>
public sealed record BybitWalletCoin
{
    public string Coin { get; init; } = string.Empty;
    public decimal Equity { get; init; }
    public decimal UsdValue { get; init; }
    public decimal WalletBalance { get; init; }
    public decimal? UnrealisedPnl { get; init; }
    public decimal Locked { get; init; }
}

public sealed record BybitWalletBalance
{
    public string AccountType { get; init; } = "UNIFIED";
    public decimal TotalEquityUsd { get; init; }
    public IReadOnlyList<BybitWalletCoin> Coins { get; init; } = [];
}

public sealed class BybitPrivateClient : IBybitPrivateClient
{
    private const string RecvWindow = "5000";
    private const string BaseUrl = "https://api.bybit.com";

    private readonly HttpClient _http;
    private readonly ILogger<BybitPrivateClient> _logger;

    public BybitPrivateClient(HttpClient http, ILogger<BybitPrivateClient> logger)
    {
        _http = http;
        _logger = logger;
        if (_http.BaseAddress is null)
            _http.BaseAddress = new Uri(BaseUrl);
    }

    public async Task<BybitWalletBalance> GetUnifiedWalletAsync(
        string apiKey,
        string apiSecret,
        CancellationToken ct = default)
    {
        var path = "/v5/account/wallet-balance";
        var query = "accountType=UNIFIED";
        var ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString(CultureInfo.InvariantCulture);
        var preSign = ts + apiKey + RecvWindow + query;
        var sign = Hmac(apiSecret, preSign);

        using var request = new HttpRequestMessage(HttpMethod.Get, $"{path}?{query}");
        request.Headers.Add("X-BAPI-API-KEY", apiKey);
        request.Headers.Add("X-BAPI-TIMESTAMP", ts);
        request.Headers.Add("X-BAPI-RECV-WINDOW", RecvWindow);
        request.Headers.Add("X-BAPI-SIGN", sign);

        using var response = await _http.SendAsync(request, ct);
        var body = await response.Content.ReadAsStringAsync(ct);

        if (!response.IsSuccessStatusCode)
        {
            _logger.LogWarning("Bybit wallet HTTP {Status}: {Body}", (int)response.StatusCode, Truncate(body, 256));
            throw new BybitApiException((int)response.StatusCode, $"HTTP {(int)response.StatusCode}");
        }

        return Parse(body);
    }

    private static BybitWalletBalance Parse(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var retCode = root.TryGetProperty("retCode", out var rcEl) ? rcEl.GetInt32() : -1;
        var retMsg = root.TryGetProperty("retMsg", out var rmEl) ? (rmEl.GetString() ?? "") : "";

        if (retCode != 0)
            throw new BybitApiException(retCode, retMsg);

        var coins = new List<BybitWalletCoin>();
        decimal totalEquityUsd = 0m;
        string accountType = "UNIFIED";

        if (root.TryGetProperty("result", out var result)
            && result.TryGetProperty("list", out var list)
            && list.ValueKind == JsonValueKind.Array)
        {
            foreach (var account in list.EnumerateArray())
            {
                if (account.TryGetProperty("accountType", out var at) && at.GetString() is { Length: > 0 } accStr)
                    accountType = accStr;

                if (account.TryGetProperty("totalEquity", out var te))
                    totalEquityUsd = ParseDecimal(te);

                if (account.TryGetProperty("coin", out var coinArr) && coinArr.ValueKind == JsonValueKind.Array)
                {
                    foreach (var c in coinArr.EnumerateArray())
                    {
                        var equity = c.TryGetProperty("equity", out var eqEl) ? ParseDecimal(eqEl) : 0m;
                        if (equity <= 0m) continue; // skip dust / zero rows

                        coins.Add(new BybitWalletCoin
                        {
                            Coin = c.TryGetProperty("coin", out var nm) ? (nm.GetString() ?? "") : "",
                            Equity = equity,
                            UsdValue = c.TryGetProperty("usdValue", out var uv) ? ParseDecimal(uv) : 0m,
                            WalletBalance = c.TryGetProperty("walletBalance", out var wb) ? ParseDecimal(wb) : 0m,
                            UnrealisedPnl = c.TryGetProperty("unrealisedPnl", out var pn) ? ParseDecimal(pn) : (decimal?)null,
                            Locked = c.TryGetProperty("locked", out var lk) ? ParseDecimal(lk) : 0m,
                        });
                    }
                }
            }
        }

        return new BybitWalletBalance
        {
            AccountType = accountType,
            TotalEquityUsd = totalEquityUsd,
            Coins = coins,
        };
    }

    private static decimal ParseDecimal(JsonElement el)
    {
        if (el.ValueKind == JsonValueKind.Number && el.TryGetDecimal(out var n)) return n;
        if (el.ValueKind == JsonValueKind.String &&
            decimal.TryParse(el.GetString(), NumberStyles.Any, CultureInfo.InvariantCulture, out var s))
            return s;
        return 0m;
    }

    private static string Hmac(string secret, string payload)
    {
        using var h = new HMACSHA256(Encoding.UTF8.GetBytes(secret));
        var bytes = h.ComputeHash(Encoding.UTF8.GetBytes(payload));
        var sb = new StringBuilder(bytes.Length * 2);
        foreach (var b in bytes) sb.Append(b.ToString("x2", CultureInfo.InvariantCulture));
        return sb.ToString();
    }

    private static string Truncate(string s, int len) =>
        string.IsNullOrEmpty(s) || s.Length <= len ? s : s[..len];
}
