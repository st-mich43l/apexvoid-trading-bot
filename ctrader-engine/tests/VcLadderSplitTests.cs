using System.Text.Json;
using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// X3: the Auto Algo XAU ladder is split 20/20/60 (40/60 over two prices). The executor already splits by each plan's own declared ratios; these cases pin that
// VolumePlanner.SplitEntryVolume gives the rounding remainder to the LAST (deepest, now the largest) slice and lets the broker minimum win over the ratio.
// The expected slices live in contracts/autotrade/xau-ladder-spec.json (auto_volume_split_cases), read by this test and described in the spec.
public class VcLadderSplitTests
{
  private static readonly SymbolInfo Xau = new("XAU", "XAUUSD", 1, 2, 1, 100, 100, 100000, 10000);

  private static JsonElement Spec()
  {
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "contracts", "autotrade", "xau-ladder-spec.json")))
    {
      dir = dir.Parent;
    }
    Assert.NotNull(dir);
    return JsonDocument.Parse(File.ReadAllText(Path.Combine(dir!.FullName, "contracts", "autotrade", "xau-ladder-spec.json"))).RootElement;
  }

  private static long Volume(decimal lots) => (long)(lots * Xau.LotSize);

  [Fact]
  public void TheSpecSplitCasesHold()
  {
    var cases = Spec().GetProperty("auto_volume_split_cases").EnumerateArray().ToArray();
    Assert.NotEmpty(cases);
    foreach (var c in cases)
    {
      var lots = decimal.Parse(c.GetProperty("lots").GetString()!, System.Globalization.CultureInfo.InvariantCulture);
      var ratios = c.GetProperty("ratios").EnumerateArray().Select(x => decimal.Parse(x.GetString()!, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
      var expected = c.GetProperty("slices_lots").EnumerateArray().Select(x => Volume(decimal.Parse(x.GetString()!, System.Globalization.CultureInfo.InvariantCulture))).ToArray();
      var slices = VolumePlanner.SplitEntryVolume(Volume(lots), Xau, ratios);
      Assert.Equal(expected, slices);
      Assert.Equal(Volume(lots), slices.Sum());
    }
  }

  [Fact]
  public void ZeroPointOneLotSplitsAs002002006()
  {
    Assert.Equal(new long[] { 200, 200, 600 }, VolumePlanner.SplitEntryVolume(1000, Xau, [0.2m, 0.2m, 0.6m]));
  }

  [Fact]
  public void ZeroPointZeroThreeLotIsOneStepPerLegTheBrokerMinimumWinning()
  {
    Assert.Equal(new long[] { 100, 100, 100 }, VolumePlanner.SplitEntryVolume(300, Xau, [0.2m, 0.2m, 0.6m]));
  }

  [Fact]
  public void TheRemainderGoesToTheLargestSliceNotTheShallowOne()
  {
    var slices = VolumePlanner.SplitEntryVolume(700, Xau, [0.2m, 0.2m, 0.6m]);
    Assert.Equal(new long[] { 100, 100, 500 }, slices);
    Assert.Equal(slices.Max(), slices[^1]);
  }

  [Fact]
  public void TheDefaultSplitIsUnchanged()
  {
    Assert.Equal(new long[] { 600, 200, 200 }, VolumePlanner.SplitEntryVolume(1000, Xau, [0.6m, 0.2m, 0.2m]));
  }
}
