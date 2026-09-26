using System.Globalization;
using System.Reflection;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// S14E: the single reviewed XAU ladder specification (contracts/autotrade/xau-ladder-spec.json),
/// C# half. The same hand-computed cases run against Manual Algo (AutoTradeEngine) and the Auto
/// Algo executor's independently-declared risk leg (TradePlanRuntime); the Python calculator is
/// held to them by algo-bot/tests/test_s14e_ladder_spec.py. The methods under test are private,
/// so they are reached by reflection: this pins behaviour without widening production visibility.
/// </summary>
public sealed class LadderSpecParityTests
{
  private static readonly JsonElement Spec = LoadSpec();

  private static JsonElement LoadSpec()
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(directory.FullName, "contracts", "autotrade", "xau-ladder-spec.json");
      if (File.Exists(candidate))
      {
        return JsonDocument.Parse(File.ReadAllText(candidate)).RootElement.Clone();
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException("contracts/autotrade/xau-ladder-spec.json was not found");
  }

  private static decimal D(JsonElement element, string name) =>
    decimal.Parse(element.GetProperty(name).GetString()!, CultureInfo.InvariantCulture);

  private static TradeDirection Dir(JsonElement element) =>
    element.GetProperty("direction").GetString() == "BUY" ? TradeDirection.Buy : TradeDirection.Sell;

  private static SymbolInfo Symbol()
  {
    var i = Spec.GetProperty("instrument");
    return new SymbolInfo(
      i.GetProperty("symbol").GetString()!, "XAUUSD", 7, Digits: i.GetProperty("digits").GetInt32(), PipPosition: 2,
      MinVolume: i.GetProperty("min_volume").GetInt64(), StepVolume: i.GetProperty("step_volume").GetInt64(),
      MaxVolume: i.GetProperty("max_volume").GetInt64(), LotSize: i.GetProperty("lot_size").GetInt64()
    );
  }

  private static decimal PipSize() => D(Spec.GetProperty("instrument"), "pip_size");

  private static MethodInfo Method(Type type, string name) =>
    type.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static)
      ?? throw new MissingMethodException(type.Name, name);

  private static object? Const(Type type, string name) =>
    // decimal "constants" compile to static readonly fields, so GetRawConstantValue would throw.
    type.GetField(name, BindingFlags.NonPublic | BindingFlags.Static)?.GetValue(null)
      ?? throw new MissingFieldException(type.Name, name);

  public static IEnumerable<object[]> EntryCases() =>
    Spec.GetProperty("entry_price_cases").EnumerateArray().Select(c => new object[] { c.GetProperty("name").GetString()! });

  public static IEnumerable<object[]> RiskPriceCases() =>
    Spec.GetProperty("risk_price_cases").EnumerateArray().Select(c => new object[] { c.GetProperty("name").GetString()! });

  public static IEnumerable<object[]> RiskVolumeCases() =>
    Spec.GetProperty("risk_volume_cases").EnumerateArray().Select(c => new object[] { c.GetProperty("equity").GetString()! });

  [Theory]
  [MemberData(nameof(EntryCases))]
  public void ManualAlgoEntryLegPricesMatchTheSpec(string name)
  {
    var c = Spec.GetProperty("entry_price_cases").EnumerateArray().Single(x => x.GetProperty("name").GetString() == name);
    var zone = new TradeCandidateZone(D(c, "zone_low"), D(c, "zone_high"));
    var result = ((decimal Shallow, decimal Deep))Method(typeof(AutoTradeEngine), "ManualEntryLegPrices")
      .Invoke(null, [zone, Dir(c), D(c, "stop"), Symbol()])!;
    Assert.Equal(D(c, "shallow"), result.Shallow);
    Assert.Equal(D(c, "deep"), result.Deep);
  }

  [Theory]
  [MemberData(nameof(RiskPriceCases))]
  public void RiskLegPriceMatchesTheSpecInManualAndAutoAlgo(string name)
  {
    var c = Spec.GetProperty("risk_price_cases").EnumerateArray().Single(x => x.GetProperty("name").GetString() == name);
    object[] args = [Dir(c), D(c, "stop"), PipSize(), Symbol()];
    Assert.Equal(D(c, "price"), (decimal)Method(typeof(AutoTradeEngine), "ManualAlgoRiskLegPrice").Invoke(null, args)!);
    Assert.Equal(D(c, "price"), (decimal)Method(typeof(TradePlanRuntime), "ReactionRiskLegPrice").Invoke(null, args)!);
  }

  [Theory]
  [MemberData(nameof(RiskVolumeCases))]
  public void RiskLegVolumeMatchesTheSpecInManualAndAutoAlgo(string equityText)
  {
    var c = Spec.GetProperty("risk_volume_cases").EnumerateArray().Single(x => x.GetProperty("equity").GetString() == equityText);
    object[] args = [D(c, "equity"), Symbol()];
    var expected = c.GetProperty("volume").GetInt64();
    Assert.Equal(expected, (long)Method(typeof(AutoTradeEngine), "ManualAlgoRiskLegVolume").Invoke(null, args)!);
    Assert.Equal(expected, (long)Method(typeof(TradePlanRuntime), "ReactionRiskLegVolume").Invoke(null, args)!);
  }

  [Fact]
  public void EveryLadderConstantMatchesTheSpecInBothPlaces()
  {
    var risk = Spec.GetProperty("risk_leg");
    foreach (var (type, prefix) in new[] { (typeof(AutoTradeEngine), "ManualAlgoRiskLeg"), (typeof(TradePlanRuntime), "ReactionRiskLeg") })
    {
      Assert.Equal(D(risk, "lots_default"), (decimal)Const(type, prefix + "LotsDefault")!);
      Assert.Equal(D(risk, "lots_below_equity_floor"), (decimal)Const(type, prefix + "LotsBelowEquityFloor")!);
      Assert.Equal(D(risk, "equity_floor"), (decimal)Const(type, prefix + "EquityFloor")!);
      Assert.Equal(D(risk, "pips_from_stop"), (decimal)Const(type, prefix + "PipsFromStop")!);
    }
    Assert.Equal(risk.GetProperty("leg_id").GetString(), (string)Const(typeof(TradePlanRuntime), "ReactionRiskLegId")!);
    var ratios = (IReadOnlyList<decimal>)typeof(AutoTradeEngine)
      .GetField("ManualEntryLegRatios", BindingFlags.NonPublic | BindingFlags.Static)!.GetValue(null)!;
    Assert.Equal(Spec.GetProperty("entry_leg_ratios").EnumerateArray().Select(e => decimal.Parse(e.GetString()!, CultureInfo.InvariantCulture)), ratios);
  }
}
