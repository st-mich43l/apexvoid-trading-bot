using System.Globalization;
using System.Text.Json;
using ApexVoid.CTraderFeed;
using ExecutionRoute = ApexVoid.CTraderFeed.AutoTradeEngine.ExecutionRoute;

namespace CTraderFeed.Tests;

/// <summary>
/// Executor half of the shared final-stop and entry-route parity fixture. The
/// same file drives `algo-bot/tests/test_protective_stop_parity.py`, so the
/// publisher and the executor cannot drift apart unnoticed.
/// </summary>
public sealed class StopContractParityTests
{
  private static readonly SymbolInfo Symbol = new(
    "XAU",
    "XAUUSD",
    7,
    Digits: 2,
    PipPosition: 2,
    MinVolume: 100,
    StepVolume: 100,
    MaxVolume: 100_000,
    LotSize: 10_000
  );

  private static readonly JsonDocument Fixture = LoadFixture();

  private static JsonDocument LoadFixture()
  {
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
      var candidate = Path.Combine(
        directory.FullName,
        "contracts",
        "autotrade",
        "final-stop-parity.json"
      );
      if (File.Exists(candidate))
      {
        return JsonDocument.Parse(File.ReadAllText(candidate));
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException(
      "contracts/autotrade/final-stop-parity.json was not found above "
      + AppContext.BaseDirectory
    );
  }

  private static IEnumerable<JsonElement> Section(string name) =>
    Fixture.RootElement.GetProperty(name).EnumerateArray();

  private static decimal Number(JsonElement element, string name) =>
    decimal.Parse(
      element.GetProperty(name).GetString()!,
      CultureInfo.InvariantCulture
    );

  public static TheoryData<string> StopCases
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var stop in Section("stops"))
      {
        data.Add(stop.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  public static TheoryData<string> ZoneIdentityCases
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var zone in Section("zone_identities"))
      {
        data.Add(zone.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  public static TheoryData<string> RouteCases
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var route in Section("routes"))
      {
        data.Add(route.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  [Theory]
  [MemberData(nameof(StopCases))]
  public void SharedStopFixtureMatchesExecutorPlanner(string name)
  {
    var root = Fixture.RootElement;
    var stop = Section("stops").Single(item =>
      item.GetProperty("name").GetString() == name
    );
    var atr = Number(root, "atr");
    var pipSize = Number(root, "pip_size");
    var sweep = stop.GetProperty("sweep_extreme");
    OpposingZoneStopContext? zone = null;
    if (stop.TryGetProperty("opposing_zone", out var zoneElement))
    {
      zone = new OpposingZoneStopContext(
        zoneElement.GetProperty("zone_id").GetString(),
        Number(zoneElement, "low"),
        Number(zoneElement, "high"),
        zoneElement.GetProperty("execution_grade").GetBoolean(),
        zoneElement.GetProperty("push_beyond_zone").GetBoolean(),
        Number(zoneElement, "buffer_atr")
      );
    }

    StructureStopPlan Plan() => StructureStopPlanner.Plan(
      stop.GetProperty("direction").GetString() == "BUY"
        ? TradeDirection.Buy
        : TradeDirection.Sell,
      Number(stop, "planned_entry"),
      Number(stop, "structure_swing"),
      atr,
      Number(stop, "structure_buffer_atr"),
      sweep.ValueKind == JsonValueKind.Null
        ? null
        : decimal.Parse(sweep.GetString()!, CultureInfo.InvariantCulture),
      Number(root, "wick_buffer_atr"),
      stop.GetProperty("minimum_stop_pips").GetInt32(),
      stop.GetProperty("maximum_stop_pips").GetInt32(),
      pipSize,
      Symbol,
      zone
    );

    if (stop.TryGetProperty("expected_error", out var expectedError))
    {
      var failure = Assert.Throws<VolumePlanningException>(() => Plan());
      Assert.Equal(expectedError.GetString(), failure.Message);
      return;
    }

    var plan = Plan();

    Assert.Equal(Number(stop, "expected_final_stop"), plan.StopLoss);
    Assert.Equal(Number(stop, "expected_final_stop_pips"), plan.StopPips);
    Assert.Equal(Number(stop, "expected_raw_stop"), plan.RawStopLoss);
    Assert.Equal(
      stop.GetProperty("expected_clamped").GetBoolean(),
      plan.Clamped
    );
    Assert.Equal(stop.GetProperty("expected_source").GetString(), plan.Source);
    Assert.Equal(
      stop.GetProperty("expected_adjustment").GetString(),
      plan.Adjustment
    );
    Assert.Equal(Number(stop, "expected_base_stop"), plan.BaseStopLoss);
    if (plan.Adjustment == "opposing_zone_push")
    {
      Assert.Equal(
        stop.GetProperty("expected_adjustment_zone_id").GetString(),
        plan.AdjustmentZoneId
      );
      Assert.Equal(
        Number(stop, "expected_adjustment_zone_low"),
        plan.AdjustmentZoneLow
      );
      Assert.Equal(
        Number(stop, "expected_adjustment_zone_high"),
        plan.AdjustmentZoneHigh
      );
    }
  }

  [Theory]
  [MemberData(nameof(ZoneIdentityCases))]
  public void SharedZoneIdentityFixtureMatchesExecutorFingerprint(string name)
  {
    var zone = Section("zone_identities").Single(item =>
      item.GetProperty("name").GetString() == name
    );
    var source = zone.GetProperty("source");
    Assert.Equal(
      zone.GetProperty("expected_fingerprint").GetString(),
      AutoTradeEngine.OpposingZoneFingerprint(
        zone.GetProperty("symbol").GetString()!,
        zone.GetProperty("timeframe").GetString()!,
        zone.GetProperty("direction").GetString()!,
        Number(zone, "zone_low"),
        Number(zone, "zone_high"),
        zone.GetProperty("created_bar_ts").GetInt64(),
        source.ValueKind == JsonValueKind.Null ? null : source.GetString()
      )
    );
  }

  [Theory]
  [MemberData(nameof(RouteCases))]
  public void SharedRouteFixtureIsHonouredByTheEntryContract(string name)
  {
    var route = Section("routes").Single(item =>
      item.GetProperty("name").GetString() == name
    );
    var declared = route.GetProperty("expected_route").GetString()!;
    var expected = declared switch
    {
      "market" => ExecutionRoute.Market,
      "single_limit" => ExecutionRoute.SingleLimit,
      _ => (ExecutionRoute?)null,
    };

    if (expected is not ExecutionRoute committed)
    {
      // An uncommitted route must accept every deterministic executor route.
      Assert.True(AutoTradeEngine.RouteInContract(declared, ExecutionRoute.Market));
      Assert.True(
        AutoTradeEngine.RouteInContract(declared, ExecutionRoute.SingleLimit)
      );
      return;
    }

    Assert.True(AutoTradeEngine.RouteInContract(declared, committed));
    foreach (var other in new[]
    {
      ExecutionRoute.Market,
      ExecutionRoute.SingleLimit,
    })
    {
      if (other == committed)
      {
        continue;
      }
      Assert.False(AutoTradeEngine.RouteInContract(declared, other));
    }
  }
}
