using System.Globalization;
using System.Reflection;
using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// The single reviewed XAU ladder specification (contracts/autotrade/xau-ladder-spec.json),
/// C# half. The hand-computed risk-leg cases run against the executor's independently-declared
/// risk leg (TradePlanRuntime); the Python calculators (manual /algo entry legs, risk leg) are
/// held to the same cases by algo-bot/tests/test_ladder_spec.py. The methods under test are
/// private, so they are reached by reflection: this pins behaviour without widening visibility.
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

  public static IEnumerable<object[]> RiskVolumeCases() =>
    Spec.GetProperty("risk_volume_cases").EnumerateArray().Select(c => new object[] { c.GetProperty("equity").GetString()! });

  // The planner (algo-bot xau_ladder.py / risk_leg.py) decides the leg's price and
  // lots from these cases; the executor places exactly the declared leg. What stays
  // pinned here is the one executor-side conversion: declared lots -> broker volume.
  [Theory]
  [MemberData(nameof(RiskVolumeCases))]
  public void DeclaredRiskLegLotsConvertToTheSpecsBrokerVolume(string equityText)
  {
    var c = Spec.GetProperty("risk_volume_cases").EnumerateArray().Single(x => x.GetProperty("equity").GetString() == equityText);
    Assert.Equal(c.GetProperty("volume").GetInt64(), VolumePlanner.VolumeForLots(D(c, "lots"), Symbol()));
  }

  public static IEnumerable<object[]> EquityTableCases() =>
    Spec.GetProperty("equity_table").GetProperty("cases").EnumerateArray().Select(c => new object[] { c.GetProperty("equity").GetString()! });

  [Theory]
  [MemberData(nameof(EquityTableCases))]
  public void OwnerEquityTableLotsMatchTheSpec(string equityText)
  {
    var c = Spec.GetProperty("equity_table").GetProperty("cases").EnumerateArray().Single(x => x.GetProperty("equity").GetString() == equityText);
    Assert.Equal(D(c, "lots"), VolumePlanner.LotsForEquity(D(c, "equity")));
  }

  [Fact]
  public void TheRiskLegIdMatchesTheSpec()
  {
    var risk = Spec.GetProperty("risk_leg");
    Assert.Equal(risk.GetProperty("leg_id").GetString(), (string)Const(typeof(TradePlanRuntime), "ReactionRiskLegId")!);
  }
}
