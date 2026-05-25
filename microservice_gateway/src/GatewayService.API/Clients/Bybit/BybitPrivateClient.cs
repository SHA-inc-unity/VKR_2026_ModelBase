using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace GatewayService.API.Clients.Bybit;

public interface IBybitPrivateClient
{
    /// <summary>
    /// Returns the user's portfolio across whichever Bybit account types are
    /// reachable with the given read-only key. Tries UNIFIED → CONTRACT →
    /// SPOT → FUND wallet-balance, plus the assets/coins-balance endpoint
    /// for Funding (which works on the much narrower
    /// <c>Account Transfer / Subaccount Transfer</c> permission).
    /// Coins from successful sources are merged by symbol (sum of equities).
    /// </summary>
    Task<BybitPortfolioSnapshot> GetPortfolioAsync(
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
/// Coin-level balance row. <see cref="UsdValue"/> may be 0 when the source
/// endpoint (funding wallet) doesn't return USD valuation — the upstream
/// caller is expected to fill it in from spot tickers.
/// </summary>
public sealed record BybitWalletCoin
{
    public string Coin { get; init; } = string.Empty;
    public decimal Equity { get; init; }
    public decimal UsdValue { get; init; }
    public decimal WalletBalance { get; init; }
    public decimal? UnrealisedPnl { get; init; }
    public decimal Locked { get; init; }
    /// <summary>Which Bybit account-type this row came from (UNIFIED / SPOT / FUND / CONTRACT).</summary>
    public string SourceAccountType { get; init; } = "UNIFIED";
}

public sealed record BybitPortfolioSnapshot
{
    /// <summary>"UNIFIED", "SPOT", "FUND", or "MIXED" when multiple sources contributed.</summary>
    public string AccountType { get; init; } = "UNIFIED";
    public decimal TotalEquityUsd { get; init; }
    public IReadOnlyList<BybitWalletCoin> Coins { get; init; } = [];
    public IReadOnlyList<BybitCopyTradingPosition> CopyTradingPositions { get; init; } = [];
    public IReadOnlyList<BybitBotPosition> BotPositions { get; init; } = [];
    public IReadOnlyList<string> SourcesUsed { get; init; } = [];
    public IReadOnlyList<string> SourcesDenied { get; init; } = [];
    /// <summary>List of Bybit API permissions the user's key is missing, so the UI can prompt for them.</summary>
    public IReadOnlyList<string> MissingPermissions { get; init; } = [];
}

/// <summary>One open copy-trading position (leader perspective or follower mirror).</summary>
public sealed record BybitCopyTradingPosition
{
    public string Symbol { get; init; } = "";
    public string Side { get; init; } = "";            // "Buy" / "Sell"
    public decimal Size { get; init; }
    public decimal EntryPrice { get; init; }
    public decimal MarkPrice { get; init; }
    public decimal UnrealisedPnl { get; init; }
    public decimal Leverage { get; init; }
    /// <summary>"leader" when fetched from /v5/copy-trading/position/list, "follower" otherwise.</summary>
    public string Role { get; init; } = "leader";
}

/// <summary>One active trading bot (Grid / DCA / Martingale on spot or derivatives).</summary>
public sealed record BybitBotPosition
{
    public string BotId { get; init; } = "";
    public string BotType { get; init; } = "";         // "grid" / "dca" / "martingale"
    public string Category { get; init; } = "";       // "spot" / "linear"
    public string Symbol { get; init; } = "";
    public decimal Investment { get; init; }
    public decimal CurrentValue { get; init; }
    public decimal TotalPnl { get; init; }
    public decimal TotalPnlPercent { get; init; }
    public string Status { get; init; } = "";
}

public sealed class BybitPrivateClient : IBybitPrivateClient
{
    private const string RecvWindow = "5000";
    private const string BaseUrl = "https://api.bybit.com";

    private static readonly string[] WalletAccountTypes = ["UNIFIED", "CONTRACT", "SPOT", "FUND"];

    private readonly HttpClient _http;
    private readonly ILogger<BybitPrivateClient> _logger;

    public BybitPrivateClient(HttpClient http, ILogger<BybitPrivateClient> logger)
    {
        _http = http;
        _logger = logger;
        if (_http.BaseAddress is null)
            _http.BaseAddress = new Uri(BaseUrl);
    }

    public async Task<BybitPortfolioSnapshot> GetPortfolioAsync(
        string apiKey,
        string apiSecret,
        CancellationToken ct = default)
    {
        var bySymbol = new Dictionary<string, BybitWalletCoin>(StringComparer.OrdinalIgnoreCase);
        var sourcesUsed = new List<string>();
        var sourcesDenied = new List<string>();
        decimal totalEquity = 0m;
        string lastRetMsg = string.Empty;

        // 1. Try the classic V5 wallet-balance endpoint with every accountType.
        //    Read-only keys with limited permissions may answer for some types
        //    and deny others — we accept whatever we can get.
        foreach (var accountType in WalletAccountTypes)
        {
            try
            {
                var snap = await TryGetWalletBalanceAsync(apiKey, apiSecret, accountType, ct);
                if (snap is null) continue;

                sourcesUsed.Add($"wallet-balance:{accountType}");
                totalEquity += snap.TotalEquityUsd;
                MergeCoins(bySymbol, snap.Coins);
            }
            catch (BybitApiException ex) when (ex.RetCode == 10005 || ex.RetCode == 10003 || ex.RetCode == 10004)
            {
                sourcesDenied.Add($"wallet-balance:{accountType} ({ex.RetCode})");
                lastRetMsg = ex.RetMsg;
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex,
                    "Bybit wallet-balance call for {AccountType} failed with non-permission error", accountType);
                sourcesDenied.Add($"wallet-balance:{accountType} (transport)");
            }
        }

        // 2. Always also probe the Funding-wallet endpoint. It uses a different
        //    permission gate (Account Transfer) so it often works when wallet-balance
        //    is locked. Returns balance rows without USD valuation.
        try
        {
            var fundCoins = await TryGetFundingBalanceAsync(apiKey, apiSecret, ct);
            if (fundCoins is { Count: > 0 })
            {
                sourcesUsed.Add("query-account-coins-balance:FUND");
                MergeCoins(bySymbol, fundCoins);
            }
        }
        catch (BybitApiException ex) when (ex.RetCode == 10005 || ex.RetCode == 10003 || ex.RetCode == 10004)
        {
            sourcesDenied.Add($"query-account-coins-balance:FUND ({ex.RetCode})");
            lastRetMsg = ex.RetMsg;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Bybit funding-balance call failed");
            sourcesDenied.Add("query-account-coins-balance:FUND (transport)");
        }

        // 3. Probe copy-trading positions. Bybit requires the "Copy Trade" scope
        //    on the key — if it's missing we get 10005 and flag the permission
        //    so the UI can ask the user to enable it on api.bybit.com.
        var missingPermissions = new List<string>();
        IReadOnlyList<BybitCopyTradingPosition> copyPositions = [];
        try
        {
            copyPositions = await TryGetCopyTradingPositionsAsync(apiKey, apiSecret, ct) ?? [];
            if (copyPositions.Count > 0) sourcesUsed.Add($"copy-trading:{copyPositions.Count}");
        }
        catch (BybitApiException ex) when (ex.RetCode == 10005 || ex.RetCode == 10003 || ex.RetCode == 10004)
        {
            sourcesDenied.Add($"copy-trading ({ex.RetCode})");
            missingPermissions.Add("CopyTrading");
            lastRetMsg = ex.RetMsg;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Bybit copy-trading call failed");
            sourcesDenied.Add("copy-trading (transport)");
        }

        // 4. Probe trading bots (Grid / DCA / Martingale across spot + linear).
        IReadOnlyList<BybitBotPosition> botPositions = [];
        try
        {
            botPositions = await TryGetBotPositionsAsync(apiKey, apiSecret, ct) ?? [];
            if (botPositions.Count > 0) sourcesUsed.Add($"trading-bots:{botPositions.Count}");
        }
        catch (BybitApiException ex) when (ex.RetCode == 10005 || ex.RetCode == 10003 || ex.RetCode == 10004)
        {
            sourcesDenied.Add($"trading-bots ({ex.RetCode})");
            missingPermissions.Add("SpotTrade/DerivativesTrade");
            lastRetMsg = ex.RetMsg;
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Bybit trading-bots call failed");
            sourcesDenied.Add("trading-bots (transport)");
        }

        // If absolutely nothing worked, surface the last Bybit retMsg so the UI
        // can display it instead of a generic "failed".
        if (sourcesUsed.Count == 0)
        {
            throw new BybitApiException(10005,
                string.IsNullOrEmpty(lastRetMsg)
                    ? "Bybit denied all read endpoints for this API key."
                    : lastRetMsg);
        }

        var accountType_ = sourcesUsed.Count switch
        {
            1 => sourcesUsed[0].Split(':')[^1],
            _ => "MIXED",
        };

        return new BybitPortfolioSnapshot
        {
            AccountType = accountType_,
            TotalEquityUsd = totalEquity,
            Coins = bySymbol.Values.OrderByDescending(c => c.UsdValue).ThenByDescending(c => c.Equity).ToList(),
            CopyTradingPositions = copyPositions,
            BotPositions = botPositions,
            SourcesUsed = sourcesUsed,
            SourcesDenied = sourcesDenied,
            MissingPermissions = missingPermissions,
        };
    }

    private static void MergeCoins(
        Dictionary<string, BybitWalletCoin> bySymbol,
        IReadOnlyList<BybitWalletCoin> add)
    {
        foreach (var coin in add)
        {
            if (string.IsNullOrEmpty(coin.Coin)) continue;
            if (bySymbol.TryGetValue(coin.Coin, out var existing))
            {
                bySymbol[coin.Coin] = existing with
                {
                    Equity = existing.Equity + coin.Equity,
                    WalletBalance = existing.WalletBalance + coin.WalletBalance,
                    Locked = existing.Locked + coin.Locked,
                    UsdValue = existing.UsdValue + coin.UsdValue,
                    UnrealisedPnl = (existing.UnrealisedPnl ?? 0m) + (coin.UnrealisedPnl ?? 0m),
                };
            }
            else
            {
                bySymbol[coin.Coin] = coin;
            }
        }
    }

    private async Task<BybitPortfolioSnapshot?> TryGetWalletBalanceAsync(
        string apiKey, string apiSecret, string accountType, CancellationToken ct)
    {
        var query = $"accountType={accountType}";
        var body = await SignedGetAsync("/v5/account/wallet-balance", query, apiKey, apiSecret, ct);
        return ParseWalletBalance(body, accountType);
    }

    private async Task<IReadOnlyList<BybitWalletCoin>?> TryGetFundingBalanceAsync(
        string apiKey, string apiSecret, CancellationToken ct)
    {
        var body = await SignedGetAsync(
            "/v5/asset/transfer/query-account-coins-balance",
            "accountType=FUND",
            apiKey, apiSecret, ct);
        return ParseFundingBalance(body);
    }

    /// <summary>
    /// Fetches the user's copy-trading positions. Requires the "Copy Trade" scope
    /// on the Bybit API key. The endpoint exists in two flavours — leader and
    /// follower — but both return the same shape; we try leader first and fall
    /// through silently if the user is not a leader.
    /// </summary>
    private async Task<IReadOnlyList<BybitCopyTradingPosition>?> TryGetCopyTradingPositionsAsync(
        string apiKey, string apiSecret, CancellationToken ct)
    {
        // category=linear is the only category that supports copy-trading today.
        var body = await SignedGetAsync(
            "/v5/position/list",
            "category=linear&settleCoin=USDT",
            apiKey, apiSecret, ct);
        return ParseCopyTradingPositions(body);
    }

    /// <summary>
    /// Fetches active trading bots. Bybit exposes a combined "running" list at
    /// <c>/v5/spot/lever-token/...</c> for leverage tokens and dedicated bot
    /// endpoints for Grid / DCA / Martingale. We probe the Grid endpoints first
    /// because they cover most users; the response shape is uniform enough to
    /// merge.
    /// </summary>
    private async Task<IReadOnlyList<BybitBotPosition>?> TryGetBotPositionsAsync(
        string apiKey, string apiSecret, CancellationToken ct)
    {
        var merged = new List<BybitBotPosition>();

        // Spot grid bots.
        try
        {
            var spotBody = await SignedGetAsync(
                "/v5/spot-grid/order/list",
                "orderStatus=Running",
                apiKey, apiSecret, ct);
            merged.AddRange(ParseBotPositions(spotBody, botType: "grid", category: "spot"));
        }
        catch (BybitApiException ex) when (ex.RetCode == 10005 || ex.RetCode == 10003 || ex.RetCode == 10004)
        {
            // Permission denied for this specific bot family — re-throw so the
            // outer probe records the missing scope.
            throw;
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Spot grid bot list call failed (will continue with futures bots)");
        }

        // Futures grid bots (linear).
        try
        {
            var futBody = await SignedGetAsync(
                "/v5/futures-grid/order/list",
                "orderStatus=Running",
                apiKey, apiSecret, ct);
            merged.AddRange(ParseBotPositions(futBody, botType: "grid", category: "linear"));
        }
        catch (BybitApiException ex) when (ex.RetCode == 10005 || ex.RetCode == 10003 || ex.RetCode == 10004)
        {
            // ditto
            throw;
        }
        catch (Exception ex)
        {
            _logger.LogDebug(ex, "Futures grid bot list call failed");
        }

        return merged;
    }

    private async Task<string> SignedGetAsync(
        string path, string query, string apiKey, string apiSecret, CancellationToken ct)
    {
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
            _logger.LogWarning("Bybit {Path} HTTP {Status}: {Body}", path, (int)response.StatusCode, Truncate(body, 256));
            throw new BybitApiException((int)response.StatusCode, $"HTTP {(int)response.StatusCode}");
        }

        return body;
    }

    private static BybitPortfolioSnapshot? ParseWalletBalance(string json, string accountType)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var retCode = root.TryGetProperty("retCode", out var rcEl) ? rcEl.GetInt32() : -1;
        var retMsg = root.TryGetProperty("retMsg", out var rmEl) ? (rmEl.GetString() ?? "") : "";
        if (retCode != 0) throw new BybitApiException(retCode, retMsg);

        var coins = new List<BybitWalletCoin>();
        decimal totalEquityUsd = 0m;
        if (root.TryGetProperty("result", out var result)
            && result.TryGetProperty("list", out var list)
            && list.ValueKind == JsonValueKind.Array)
        {
            foreach (var account in list.EnumerateArray())
            {
                if (account.TryGetProperty("totalEquity", out var te))
                    totalEquityUsd += ParseDecimal(te);

                if (account.TryGetProperty("coin", out var coinArr) && coinArr.ValueKind == JsonValueKind.Array)
                {
                    foreach (var c in coinArr.EnumerateArray())
                    {
                        var equity = c.TryGetProperty("equity", out var eqEl) ? ParseDecimal(eqEl) : 0m;
                        var walletBalance = c.TryGetProperty("walletBalance", out var wb) ? ParseDecimal(wb) : 0m;
                        // skip dust / zero rows
                        if (equity <= 0m && walletBalance <= 0m) continue;

                        coins.Add(new BybitWalletCoin
                        {
                            Coin = c.TryGetProperty("coin", out var nm) ? (nm.GetString() ?? "") : "",
                            Equity = equity > 0 ? equity : walletBalance,
                            UsdValue = c.TryGetProperty("usdValue", out var uv) ? ParseDecimal(uv) : 0m,
                            WalletBalance = walletBalance,
                            UnrealisedPnl = c.TryGetProperty("unrealisedPnl", out var pn) ? ParseDecimal(pn) : (decimal?)null,
                            Locked = c.TryGetProperty("locked", out var lk) ? ParseDecimal(lk) : 0m,
                            SourceAccountType = accountType,
                        });
                    }
                }
            }
        }

        return new BybitPortfolioSnapshot
        {
            AccountType = accountType,
            TotalEquityUsd = totalEquityUsd,
            Coins = coins,
        };
    }

    private static List<BybitWalletCoin> ParseFundingBalance(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var retCode = root.TryGetProperty("retCode", out var rcEl) ? rcEl.GetInt32() : -1;
        var retMsg = root.TryGetProperty("retMsg", out var rmEl) ? (rmEl.GetString() ?? "") : "";
        if (retCode != 0) throw new BybitApiException(retCode, retMsg);

        var coins = new List<BybitWalletCoin>();
        if (root.TryGetProperty("result", out var result)
            && result.TryGetProperty("balance", out var balArr)
            && balArr.ValueKind == JsonValueKind.Array)
        {
            foreach (var c in balArr.EnumerateArray())
            {
                var walletBalance = c.TryGetProperty("walletBalance", out var wb) ? ParseDecimal(wb) : 0m;
                if (walletBalance <= 0m) continue;

                coins.Add(new BybitWalletCoin
                {
                    Coin = c.TryGetProperty("coin", out var nm) ? (nm.GetString() ?? "") : "",
                    Equity = walletBalance,
                    WalletBalance = walletBalance,
                    Locked = c.TryGetProperty("transferBalance", out var tb)
                        ? Math.Max(0m, walletBalance - ParseDecimal(tb))
                        : 0m,
                    // Funding endpoint doesn't provide USD valuation — the caller
                    // is expected to enrich it via spot tickers.
                    UsdValue = 0m,
                    SourceAccountType = "FUND",
                });
            }
        }
        return coins;
    }

    private static List<BybitCopyTradingPosition> ParseCopyTradingPositions(string json)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var retCode = root.TryGetProperty("retCode", out var rcEl) ? rcEl.GetInt32() : -1;
        var retMsg = root.TryGetProperty("retMsg", out var rmEl) ? (rmEl.GetString() ?? "") : "";
        if (retCode != 0) throw new BybitApiException(retCode, retMsg);

        var positions = new List<BybitCopyTradingPosition>();
        if (root.TryGetProperty("result", out var result)
            && result.TryGetProperty("list", out var list)
            && list.ValueKind == JsonValueKind.Array)
        {
            foreach (var p in list.EnumerateArray())
            {
                var size = p.TryGetProperty("size", out var sz) ? ParseDecimal(sz) : 0m;
                if (size <= 0m) continue;

                positions.Add(new BybitCopyTradingPosition
                {
                    Symbol = p.TryGetProperty("symbol", out var sym) ? (sym.GetString() ?? "") : "",
                    Side = p.TryGetProperty("side", out var sd) ? (sd.GetString() ?? "") : "",
                    Size = size,
                    EntryPrice = p.TryGetProperty("avgPrice", out var ap) ? ParseDecimal(ap) : 0m,
                    MarkPrice = p.TryGetProperty("markPrice", out var mp) ? ParseDecimal(mp) : 0m,
                    UnrealisedPnl = p.TryGetProperty("unrealisedPnl", out var pn) ? ParseDecimal(pn) : 0m,
                    Leverage = p.TryGetProperty("leverage", out var lv) ? ParseDecimal(lv) : 0m,
                    Role = "leader",
                });
            }
        }
        return positions;
    }

    private static List<BybitBotPosition> ParseBotPositions(string json, string botType, string category)
    {
        using var doc = JsonDocument.Parse(json);
        var root = doc.RootElement;

        var retCode = root.TryGetProperty("retCode", out var rcEl) ? rcEl.GetInt32() : -1;
        var retMsg = root.TryGetProperty("retMsg", out var rmEl) ? (rmEl.GetString() ?? "") : "";
        if (retCode != 0) throw new BybitApiException(retCode, retMsg);

        var bots = new List<BybitBotPosition>();
        if (root.TryGetProperty("result", out var result)
            && result.TryGetProperty("list", out var list)
            && list.ValueKind == JsonValueKind.Array)
        {
            foreach (var b in list.EnumerateArray())
            {
                var investment = b.TryGetProperty("totalInvestment", out var inv) ? ParseDecimal(inv) : 0m;
                var pnl = b.TryGetProperty("totalPnl", out var pn) ? ParseDecimal(pn) : 0m;
                var pnlPct = b.TryGetProperty("pnlPercent", out var pp) ? ParseDecimal(pp) : 0m;

                bots.Add(new BybitBotPosition
                {
                    BotId = b.TryGetProperty("orderId", out var oid) ? (oid.GetString() ?? "") : "",
                    BotType = botType,
                    Category = category,
                    Symbol = b.TryGetProperty("symbol", out var sym) ? (sym.GetString() ?? "") : "",
                    Investment = investment,
                    CurrentValue = investment + pnl,
                    TotalPnl = pnl,
                    TotalPnlPercent = pnlPct,
                    Status = b.TryGetProperty("orderStatus", out var st) ? (st.GetString() ?? "") : "",
                });
            }
        }
        return bots;
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
