using GatewayService.API.Common;
using GatewayService.API.DTOs.Responses;
using GatewayService.API.Kafka;
using GatewayService.API.Market;
using System.Globalization;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace GatewayService.API.Clients.Market;

public sealed partial class MarketServiceClient
{
    private static string? GetString(JsonElement item, string name)
    {
        return item.TryGetProperty(name, out var property)
            ? property.GetString()
            : null;
    }

    private static decimal GetDecimal(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return 0;
        }

        return property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetDecimal(out var numberValue) => numberValue,
            JsonValueKind.String when decimal.TryParse(property.GetString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0,
        };
    }


    private static int TryGetInt32(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return 0;
        }

        return property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetInt32(out var numberValue) => numberValue,
            JsonValueKind.Number when property.TryGetInt64(out var longValue) => (int)longValue,
            JsonValueKind.String when int.TryParse(property.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0,
        };
    }

    private static decimal? TryGetNestedDecimal(JsonElement item, string container, string propertyName)
    {
        if (!item.TryGetProperty(container, out var containerEl) || containerEl.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var value = GetDecimal(containerEl, propertyName);
        return value > 0 ? value : null;
    }

    private static DateTimeOffset? TryGetUnixSeconds(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        long seconds = property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetInt64(out var numberValue) => numberValue,
            JsonValueKind.String when long.TryParse(property.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => 0,
        };

        return seconds > 0 ? DateTimeOffset.FromUnixTimeSeconds(seconds) : null;
    }

    private static DateTimeOffset? GetDateTimeOffset(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        return property.ValueKind switch
        {
            JsonValueKind.String when DateTimeOffset.TryParse(
                property.GetString(),
                CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                out var dateValue) => dateValue,
            _ => null,
        };
    }

    private static long? GetLong(JsonElement item, string name)
    {
        if (!item.TryGetProperty(name, out var property))
        {
            return null;
        }

        return property.ValueKind switch
        {
            JsonValueKind.Number when property.TryGetInt64(out var numberValue) => numberValue,
            JsonValueKind.String when long.TryParse(property.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var stringValue) => stringValue,
            _ => null,
        };
    }

    private static string? NormalizeExchange(string? exchange)
    {
        return string.IsNullOrWhiteSpace(exchange)
            ? null
            : exchange.Trim().ToLowerInvariant();
    }

    private static string NormalizeAsset(string asset) => asset?.Trim().ToUpperInvariant() ?? string.Empty;

    private static string ExtractBaseAsset(string symbol)
    {
        var normalized = NormalizeAsset(symbol);
        return normalized.EndsWith("USDT", StringComparison.OrdinalIgnoreCase) && normalized.Length > 4
            ? normalized[..^4]
            : normalized;
    }

    private static string ExtractQuoteAsset(string symbol)
    {
        var normalized = NormalizeAsset(symbol);
        return normalized.EndsWith("USDT", StringComparison.OrdinalIgnoreCase)
            ? "USDT"
            : string.Empty;
    }

    private static string? BuildLogoUrl(string baseAsset)
    {
        if (string.IsNullOrWhiteSpace(baseAsset))
        {
            return null;
        }

        return $"https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color/{baseAsset.ToLowerInvariant()}.svg";
    }
}
