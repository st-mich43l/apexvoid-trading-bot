using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// Owner 25 Nov 2026: every TP level books a partial of the filled volume by the plan's close ratios (VolumePlanner.PlanTargetShare). XAU: 0.01 lot = 100 units,
// broker minimum and step 0.01. The plan's targets are 40/20/20/20 (TP1..TP4). Each case walks the four targets on a fixed fill and books what the executor would.
public class TpPartialShareTests
{
  private static readonly SymbolInfo Xau = new("XAU", "XAUUSD", 1, 2, 1, 100, 100, 100000, 10000);
  private static readonly decimal[] Ratios = [0.4m, 0.2m, 0.2m, 0.2m];

  private static long[] Walk(long filled, decimal[] ratios)
  {
    var booked = new long[ratios.Length];
    var remaining = filled;
    for (var i = 0; i < ratios.Length && remaining > 0; i++)
    {
      var isFinal = i == ratios.Length - 1;
      var share = isFinal ? remaining : VolumePlanner.PlanTargetShare(remaining, ratios[i..], Xau);
      var close = !isFinal && share <= 0 ? 0 : VolumePlanner.PlanPartialCloseVolume(remaining, share, Xau, isFinal);
      booked[i] = close;
      remaining -= close;
    }
    Assert.Equal(filled, booked.Sum());
    return booked;
  }

  [Theory]
  // 0.12 lots: 40/20/20/20 of 12 steps with no rounding loss.
  [InlineData(1200, new long[] { 500, 200, 200, 300 })]
  // 0.04 lots: every TP books 0.01 (the old two-step floor booked 0, 0, 0.02, 0.02).
  [InlineData(400, new long[] { 100, 100, 100, 100 })]
  // 0.02 lots: TP1 books 0.01 and the final TP keeps the last 0.01.
  [InlineData(200, new long[] { 100, 0, 0, 100 })]
  // 0.03 lots: TP1 and TP2 book 0.01 each, the final TP keeps 0.01.
  [InlineData(300, new long[] { 100, 100, 0, 100 })]
  // 0.01 lot: nothing to split, the final TP closes it.
  [InlineData(100, new long[] { 0, 0, 0, 100 })]
  // 0.08 lots: 0.03 / 0.01 / 0.02 / 0.02 (TP1 quota 3.2 steps; at TP2 the 5 steps left split 1/2/2, ties going to the later targets).
  [InlineData(800, new long[] { 300, 100, 200, 200 })]
  // 0.25 lots.
  [InlineData(2500, new long[] { 1000, 500, 500, 500 })]
  public void EveryTargetBooksAPartialOfTheFilledVolume(long filled, long[] expected)
  {
    Assert.Equal(expected, Walk(filled, Ratios));
  }

  [Fact]
  public void EveryTargetGetsAtLeastTheBrokerMinimumWhenTheVolumeAllowsIt()
  {
    for (var filled = 400L; filled <= 5000; filled += 100)
    {
      var booked = Walk(filled, Ratios);
      Assert.All(booked, b => Assert.True(b >= 100, $"filled {filled}: {string.Join(",", booked)}"));
    }
  }

  [Fact]
  public void AnyNumberOfTargetsAndAnyRatiosAreSplitWithoutLosingVolume()
  {
    decimal[][] plans = [[0.5m, 0.5m], [0.25m, 0.25m, 0.25m, 0.25m], [0.6m, 0.2m, 0.1m, 0.05m, 0.05m], [1m, 1m, 1m]];
    foreach (var ratios in plans)
    {
      for (var filled = 100L; filled <= 3000; filled += 100)
      {
        var booked = Walk(filled, ratios);
        Assert.True(booked[^1] > 0, "the final target always keeps volume");
      }
    }
  }

  [Fact]
  public void TheFinalTargetClosesEverything()
  {
    Assert.Equal(700, VolumePlanner.PlanTargetShare(700, [0.2m], Xau));
  }
}
