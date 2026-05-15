using FluentAssertions;
using GatewayService.API.Market;
using Xunit;

namespace GatewayService.UnitTests.Market;

public sealed class TimeframeMapTests
{
    [Fact]
    public void All_timeframes_have_unique_ids()
    {
        var ids = TimeframeMap.All.Select(tf => tf.Id).ToList();
        ids.Distinct().Should().HaveCount(ids.Count);
    }

    [Fact]
    public void All_timeframes_have_unique_bybit_intervals()
    {
        var intervals = TimeframeMap.All.Select(tf => tf.BybitInterval).ToList();
        intervals.Distinct().Should().HaveCount(intervals.Count);
    }

    [Theory]
    [InlineData("1m")]
    [InlineData("3m")]
    [InlineData("5m")]
    [InlineData("15m")]
    [InlineData("30m")]
    [InlineData("60m")]
    [InlineData("120m")]
    [InlineData("240m")]
    [InlineData("360m")]
    [InlineData("720m")]
    [InlineData("1d")]
    public void IsValid_returns_true_for_all_known_ids(string id)
    {
        TimeframeMap.IsValid(id).Should().BeTrue();
    }

    [Theory]
    [InlineData("5")]
    [InlineData("D")]
    [InlineData("")]
    [InlineData("1h")]
    [InlineData("daily")]
    public void IsValid_returns_false_for_unknown_ids(string id)
    {
        TimeframeMap.IsValid(id).Should().BeFalse();
    }

    [Fact]
    public void TryGetById_returns_correct_info_for_5m()
    {
        TimeframeMap.TryGetById("5m", out var info).Should().BeTrue();
        info.Should().NotBeNull();
        info!.BybitInterval.Should().Be("5");
        info.StepMs.Should().Be(300_000);
        info.Class.Should().Be(TimeframeClass.Heavy);
    }

    [Fact]
    public void TryGetById_returns_false_for_unknown()
    {
        TimeframeMap.TryGetById("99x", out var info).Should().BeFalse();
        info.Should().BeNull();
    }

    [Fact]
    public void GetById_throws_for_unknown()
    {
        var act = () => TimeframeMap.GetById("invalid");
        act.Should().Throw<ArgumentException>();
    }

    [Fact]
    public void All_heavy_timeframes_have_class_heavy()
    {
        var heavyIds = new[] { "1m", "3m", "5m" };
        foreach (var id in heavyIds)
        {
            TimeframeMap.GetById(id).Class.Should().Be(TimeframeClass.Heavy,
                because: $"'{id}' should be Heavy");
        }
    }

    [Fact]
    public void All_medium_timeframes_have_class_medium()
    {
        var mediumIds = new[] { "15m", "30m", "60m", "120m", "240m" };
        foreach (var id in mediumIds)
        {
            TimeframeMap.GetById(id).Class.Should().Be(TimeframeClass.Medium,
                because: $"'{id}' should be Medium");
        }
    }

    [Fact]
    public void All_light_timeframes_have_class_light()
    {
        var lightIds = new[] { "360m", "720m", "1d" };
        foreach (var id in lightIds)
        {
            TimeframeMap.GetById(id).Class.Should().Be(TimeframeClass.Light,
                because: $"'{id}' should be Light");
        }
    }

    [Fact]
    public void Daily_timeframe_maps_to_D_bybit_interval()
    {
        TimeframeMap.GetById("1d").BybitInterval.Should().Be("D");
    }

    [Fact]
    public void All_has_exactly_eleven_entries()
    {
        TimeframeMap.All.Should().HaveCount(11);
    }
}
