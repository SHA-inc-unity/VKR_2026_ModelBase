using FluentAssertions;
using GatewayService.API.Market;
using Xunit;

namespace GatewayService.UnitTests.Market;

public sealed class CandleCountGridTests
{
    [Fact]
    public void Heavy_grid_max_is_500()
    {
        CandleCountGrid.MaxFor(TimeframeClass.Heavy).Should().Be(500);
    }

    [Fact]
    public void Medium_grid_max_is_1000()
    {
        CandleCountGrid.MaxFor(TimeframeClass.Medium).Should().Be(1000);
    }

    [Fact]
    public void Light_grid_max_is_2000()
    {
        CandleCountGrid.MaxFor(TimeframeClass.Light).Should().Be(2000);
    }

    [Theory]
    [InlineData(50,  TimeframeClass.Heavy,  true)]
    [InlineData(100, TimeframeClass.Heavy,  true)]
    [InlineData(200, TimeframeClass.Heavy,  true)]
    [InlineData(500, TimeframeClass.Heavy,  true)]
    [InlineData(1000, TimeframeClass.Heavy, false)]  // exceeds heavy cap
    [InlineData(2000, TimeframeClass.Heavy, false)]
    [InlineData(50,   TimeframeClass.Medium, true)]
    [InlineData(1000, TimeframeClass.Medium, true)]
    [InlineData(2000, TimeframeClass.Medium, false)] // exceeds medium cap
    [InlineData(2000, TimeframeClass.Light,  true)]
    [InlineData(150,  TimeframeClass.Heavy,  false)] // not in grid
    [InlineData(0,    TimeframeClass.Heavy,  false)]
    [InlineData(-1,   TimeframeClass.Light,  false)]
    public void IsValid_matches_grid(int count, TimeframeClass cls, bool expected)
    {
        CandleCountGrid.IsValid(count, cls).Should().Be(expected);
    }

    [Fact]
    public void ForClass_heavy_returns_correct_sequence()
    {
        CandleCountGrid.ForClass(TimeframeClass.Heavy)
            .Should().ContainInOrder(50, 100, 200, 500)
            .And.HaveCount(4);
    }

    [Fact]
    public void ForClass_light_contains_2000()
    {
        CandleCountGrid.ForClass(TimeframeClass.Light).Should().Contain(2000);
    }

    [Fact]
    public void All_grids_are_strictly_ascending()
    {
        foreach (var cls in Enum.GetValues<TimeframeClass>())
        {
            var grid = CandleCountGrid.ForClass(cls).ToList();
            for (var i = 1; i < grid.Count; i++)
                grid[i].Should().BeGreaterThan(grid[i - 1],
                    because: $"{cls} grid should be ascending at index {i}");
        }
    }
}
