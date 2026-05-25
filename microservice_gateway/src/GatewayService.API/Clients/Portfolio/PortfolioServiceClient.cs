using GatewayService.API.Clients.Account;
using GatewayService.API.Clients.Bybit;
using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Frontend;
using Microsoft.Extensions.Caching.Memory;

namespace GatewayService.API.Clients.Portfolio;

/// <summary>
/// Resolves the user's portfolio by:
/// 1. Asking microservice_account for a decrypted Bybit API key.
/// 2. Calling Bybit V5 /v5/account/wallet-balance with HMAC signing.
/// 3. Mapping coin rows into the frontend portfolio summary contract.
///
/// Falls back to the legacy in-memory stub when no key is configured,
/// signaling <c>State = "no_key"</c> so the Flutter screen can render the
/// "Connect Bybit" CTA instead of a confusing empty table.
/// </summary>
public sealed class PortfolioServiceClient : IPortfolioServiceClient
{
    private static readonly TimeSpan CacheTtl = TimeSpan.FromSeconds(30);

    private readonly IFrontendContractState _state;
    private readonly IAccountInternalClient _account;
    private readonly IBybitPrivateClient _bybit;
    private readonly IMemoryCache _cache;
    private readonly ILogger<PortfolioServiceClient> _logger;

    public PortfolioServiceClient(
        IFrontendContractState state,
        IAccountInternalClient account,
        IBybitPrivateClient bybit,
        IMemoryCache cache,
        ILogger<PortfolioServiceClient> logger)
    {
        _state = state;
        _account = account;
        _bybit = bybit;
        _cache = cache;
        _logger = logger;
    }

    public Task<ServiceResult<PortfolioSummaryDto>> GetSummaryAsync(string userId, CancellationToken ct = default)
    {
        // Dashboard summary stays on the legacy stub for now — it's only a
        // small "total value" tile and the home screen tolerates an empty one.
        return Task.FromResult(ServiceResult<PortfolioSummaryDto>.Ok(_state.GetDashboardPortfolioSummary(userId)));
    }

    public async Task<ServiceResult<PortfolioDetailedSummaryResponse>> GetDetailedSummaryAsync(string userId, CancellationToken ct = default)
    {
        if (!Guid.TryParse(userId, out var userGuid))
        {
            // Guest / malformed token — surface as no_key so the UI renders the CTA.
            return ServiceResult<PortfolioDetailedSummaryResponse>.Ok(EmptyNoKey());
        }

        var cacheKey = $"portfolio:user:{userGuid}";
        if (_cache.TryGetValue(cacheKey, out PortfolioDetailedSummaryResponse? cached) && cached is not null)
            return ServiceResult<PortfolioDetailedSummaryResponse>.Ok(cached);

        var key = await _account.GetActiveKeyAsync(userGuid, "bybit", ct);
        if (key is null)
        {
            var empty = EmptyNoKey();
            _cache.Set(cacheKey, empty, CacheTtl);
            return ServiceResult<PortfolioDetailedSummaryResponse>.Ok(empty);
        }

        try
        {
            var wallet = await _bybit.GetPortfolioAsync(key.ApiKey, key.ApiSecret, ct);
            var dto = MapToDetailedSummary(wallet);
            _cache.Set(cacheKey, dto, CacheTtl);
            return ServiceResult<PortfolioDetailedSummaryResponse>.Ok(dto);
        }
        catch (BybitApiException ex)
        {
            _logger.LogWarning("Bybit wallet fetch failed for {UserId}: retCode={Code} msg={Msg}", userGuid, ex.RetCode, ex.RetMsg);
            var err = new PortfolioDetailedSummaryResponse
            {
                State = "error",
                Message = ex.RetMsg,
                ByAsset = [],
                ByExchange = [],
            };
            // Short cache (15 s) so we don't hammer Bybit while the user fixes their key.
            _cache.Set(cacheKey, err, TimeSpan.FromSeconds(15));
            return ServiceResult<PortfolioDetailedSummaryResponse>.Ok(err);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogWarning(ex, "Unexpected error fetching portfolio for {UserId}", userGuid);
            return ServiceResult<PortfolioDetailedSummaryResponse>.Ok(new PortfolioDetailedSummaryResponse
            {
                State = "error",
                Message = "Bybit request failed",
                ByAsset = [],
                ByExchange = [],
            });
        }
    }

    private static PortfolioDetailedSummaryResponse EmptyNoKey() => new()
    {
        State = "no_key",
        TotalValue = 0,
        TotalPnl = 0,
        TotalPnlPercent = 0,
        AssetCount = 0,
        ExchangeCount = 0,
        ByAsset = [],
        ByExchange = [],
    };

    private static PortfolioDetailedSummaryResponse MapToDetailedSummary(BybitPortfolioSnapshot wallet)
    {
        var byAsset = wallet.Coins
            .OrderByDescending(c => c.UsdValue)
            .Select(c => new PortfolioAssetSummaryDto
            {
                Symbol = c.Coin,
                TotalAmount = c.Equity,
                TotalValue = c.UsdValue,
                Change24h = 0m, // Best-effort; live ticker overlay supplies % on the UI side.
                ExchangeBreakdown =
                [
                    new PortfolioAssetExchangeBreakdownDto
                    {
                        Exchange = "Bybit",
                        Amount = c.Equity,
                        Value = c.UsdValue,
                    }
                ],
            })
            .ToList();

        var totalValue = wallet.TotalEquityUsd > 0
            ? wallet.TotalEquityUsd
            : byAsset.Sum(a => a.TotalValue);

        var byExchange = new List<PortfolioExchangeSummaryDto>
        {
            new()
            {
                Exchange = "Bybit",
                TotalValue = totalValue,
                Change24h = 0m,
                IsSynced = true,
                LastSyncedAt = DateTimeOffset.UtcNow,
                Holdings = byAsset.Select(a => new PortfolioExchangeHoldingDto
                {
                    Symbol = a.Symbol,
                    Amount = a.TotalAmount,
                    Value = a.TotalValue,
                    Change24h = a.Change24h,
                }).ToList(),
            }
        };

        var copyTrading = wallet.CopyTradingPositions
            .Select(p => new PortfolioCopyTradingDto
            {
                Symbol = p.Symbol,
                Side = p.Side,
                Size = p.Size,
                EntryPrice = p.EntryPrice,
                MarkPrice = p.MarkPrice,
                UnrealisedPnl = p.UnrealisedPnl,
                Leverage = p.Leverage,
                Role = p.Role,
            })
            .ToList();

        var bots = wallet.BotPositions
            .Select(b => new PortfolioBotDto
            {
                BotId = b.BotId,
                BotType = b.BotType,
                Category = b.Category,
                Symbol = b.Symbol,
                Investment = b.Investment,
                CurrentValue = b.CurrentValue,
                TotalPnl = b.TotalPnl,
                TotalPnlPercent = b.TotalPnlPercent,
                Status = b.Status,
            })
            .ToList();

        // Fold unrealised PnL from copy-trading + bots into the totals so the
        // "Total value" tile reflects the full picture, not just spot equity.
        var copyPnl = copyTrading.Sum(p => p.UnrealisedPnl);
        var botPnl = bots.Sum(b => b.TotalPnl);
        var grandTotal = totalValue + copyPnl + bots.Sum(b => b.CurrentValue);

        return new PortfolioDetailedSummaryResponse
        {
            State = "ok",
            TotalValue = grandTotal,
            TotalPnl = copyPnl + botPnl,
            TotalPnlPercent = grandTotal > 0 ? (copyPnl + botPnl) / grandTotal * 100m : 0m,
            AssetCount = byAsset.Count,
            ExchangeCount = byExchange.Count,
            ByAsset = byAsset,
            ByExchange = byExchange,
            CopyTrading = copyTrading,
            Bots = bots,
            MissingPermissions = wallet.MissingPermissions,
        };
    }
}
