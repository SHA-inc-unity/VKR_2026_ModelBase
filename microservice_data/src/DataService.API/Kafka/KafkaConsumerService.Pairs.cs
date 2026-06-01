using System.IO.Compression;
using System.IO.Pipelines;
using System.Text.Json;
using Confluent.Kafka;
using DataService.API.Bybit;
using DataService.API.Database;
using DataService.API.Dataset;
using DataService.API.Jobs;
using DataService.API.Markets;
using DataService.API.Minio;
using DataService.API.Settings;
using Microsoft.Extensions.Options;
using Npgsql;

namespace DataService.API.Kafka;

public sealed partial class KafkaConsumerService
{
    // ── Currency pairs center (single source of truth) ──────────────────

    private async Task<object> HandlePairsListAsync(CancellationToken ct) =>
        await BuildPairsStateAsync(ct);

    private async Task<object> HandlePairsAddAsync(JsonElement payload, CancellationToken ct)
    {
        var role = TryGetString(payload, "role");
        var asset = TryGetString(payload, "asset");
        if (string.IsNullOrWhiteSpace(role) || string.IsNullOrWhiteSpace(asset))
            return new { error = "role and asset are required", code = "bad_request" };
        try { await _pairsRepo.AddAsync(role!, asset!, ct); }
        catch (ArgumentException ex) { return new { error = ex.Message, code = "bad_request" }; }
        await NotifyPairsChangedAsync("add", role!, asset!, ct);
        return await BuildPairsStateAsync(ct);
    }

    private async Task<object> HandlePairsRemoveAsync(JsonElement payload, CancellationToken ct)
    {
        var role = TryGetString(payload, "role");
        var asset = TryGetString(payload, "asset");
        if (string.IsNullOrWhiteSpace(role) || string.IsNullOrWhiteSpace(asset))
            return new { error = "role and asset are required", code = "bad_request" };
        try { await _pairsRepo.RemoveAsync(role!, asset!, ct); }
        catch (ArgumentException ex) { return new { error = ex.Message, code = "bad_request" }; }
        await NotifyPairsChangedAsync("remove", role!, asset!, ct);
        return await BuildPairsStateAsync(ct);
    }

    private async Task<object> HandlePairsSetActiveAsync(JsonElement payload, CancellationToken ct)
    {
        var role = TryGetString(payload, "role");
        var asset = TryGetString(payload, "asset");
        var active = TryGetBool(payload, "active");
        if (string.IsNullOrWhiteSpace(role) || string.IsNullOrWhiteSpace(asset) || active is null)
            return new { error = "role, asset and active are required", code = "bad_request" };
        try { await _pairsRepo.SetActiveAsync(role!, asset!, active.Value, ct); }
        catch (ArgumentException ex) { return new { error = ex.Message, code = "bad_request" }; }
        await NotifyPairsChangedAsync("set_active", role!, asset!, ct);
        return await BuildPairsStateAsync(ct);
    }

    private async Task<object> BuildPairsStateAsync(CancellationToken ct)
    {
        var bases   = await _pairsRepo.ListAsync(CurrencyPairsRepository.RoleBase, ct);
        var quotes  = await _pairsRepo.ListAsync(CurrencyPairsRepository.RoleQuote, ct);
        var symbols = await _pairsRepo.GetActiveSymbolsAsync(ct);
        return new
        {
            bases   = bases.Select(x => new { asset = x.Asset, active = x.Active }),
            quotes  = quotes.Select(x => new { asset = x.Asset, active = x.Active }),
            symbols,
        };
    }

    private async Task NotifyPairsChangedAsync(string operation, string role, string asset, CancellationToken ct)
    {
        // Hot-reload the running market watcher so add/remove takes effect
        // without a container restart, and emit a fire-and-forget event so the
        // admin (and any other consumer) can live-refresh.
        _marketWatcher.RequestReload();
        await _producer.PublishEventAsync(Topics.EvtDataPairsChanged, new
        {
            operation,
            role = role.Trim().ToLowerInvariant(),
            asset = asset.Trim().ToUpperInvariant(),
            at = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        }, ct);
    }
}
