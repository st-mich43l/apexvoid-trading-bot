using System.Text.Json;
using System.Text.Json.Nodes;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

/// <summary>
/// Executor half of the shared TradePlan V8 contract fixture. The same file
/// drives `algo-bot/tests/test_trade_plan_v8_contract.py`, so the plan
/// Python builds and the plan C# parses/validates cannot drift apart
/// unnoticed.
/// </summary>
public sealed class TradePlanV8ContractTests
{
  private static readonly JsonSerializerOptions ReadOptions = new()
  {
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
  };

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
        "trade-plan-v8.json"
      );
      if (File.Exists(candidate))
      {
        return JsonDocument.Parse(File.ReadAllText(candidate));
      }
      directory = directory.Parent;
    }
    throw new FileNotFoundException(
      "contracts/autotrade/trade-plan-v8.json was not found above "
      + AppContext.BaseDirectory
    );
  }

  private static JsonObject ValidPlan(string name)
  {
    foreach (var entry in Fixture.RootElement.GetProperty("valid_plans").EnumerateArray())
    {
      if (entry.GetProperty("name").GetString() == name)
      {
        return JsonNode.Parse(entry.GetProperty("plan").GetRawText())!.AsObject();
      }
    }
    throw new InvalidOperationException($"no valid_plans entry named {name}");
  }

  private static void ApplyOverride(JsonObject plan, string path, JsonElement value)
  {
    var parts = path.Split('.');
    JsonNode node = plan;
    for (var i = 0; i < parts.Length - 1; i++)
    {
      node = int.TryParse(parts[i], out var index)
        ? node.AsArray()[index]!
        : node[parts[i]]!;
    }
    var last = parts[^1];
    var jsonValue = value.ValueKind == JsonValueKind.Null
      ? null
      : JsonNode.Parse(value.GetRawText());
    if (int.TryParse(last, out var lastIndex))
    {
      node.AsArray()[lastIndex] = jsonValue;
    }
    else
    {
      node[last] = jsonValue;
    }
  }

  private static JsonObject BuildInvalidPlan(JsonElement testCase)
  {
    var overrides = testCase.GetProperty("plan_overrides");
    var basePlan = ValidPlan(overrides.GetProperty("base").GetString()!);
    foreach (var prop in overrides.EnumerateObject())
    {
      if (prop.Name == "base")
      {
        continue;
      }
      ApplyOverride(basePlan, prop.Name, prop.Value);
    }
    return basePlan;
  }

  public static TheoryData<string> ValidPlanNames
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var entry in Fixture.RootElement.GetProperty("valid_plans").EnumerateArray())
      {
        data.Add(entry.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  public static TheoryData<string> InvalidPlanNames
  {
    get
    {
      var data = new TheoryData<string>();
      foreach (var entry in Fixture.RootElement.GetProperty("invalid_plans").EnumerateArray())
      {
        data.Add(entry.GetProperty("name").GetString()!);
      }
      return data;
    }
  }

  [Theory]
  [MemberData(nameof(ValidPlanNames))]
  public void SharedValidPlanParsesAndValidates(string name)
  {
    var plan = JsonSerializer.Deserialize<TradePlan>(
      ValidPlan(name).ToJsonString(),
      ReadOptions
    );

    Assert.NotNull(plan);
    Assert.Equal(8, plan!.Version);
    TradePlanValidator.Validate(plan);
  }

  [Theory]
  [MemberData(nameof(ValidPlanNames))]
  public void SharedValidPlanUsesProductionSourceGeneratedMetadata(string name)
  {
    var plan = JsonSerializer.Deserialize(
      ValidPlan(name).ToJsonString(),
      AutoTradeJsonContext.Default.TradePlan
    );

    Assert.NotNull(plan);
    Assert.Equal(8, plan!.Version);
    Assert.NotEmpty(plan.Targets);
    TradePlanValidator.Validate(plan);
  }

  [Fact]
  public void ProductionV8ContractSelfTestPasses()
  {
    TradePlanJson.AssertContractAvailable();
  }

  [Theory]
  [MemberData(nameof(InvalidPlanNames))]
  public void SharedInvalidPlanIsRejected(string name)
  {
    JsonElement testCase = default;
    foreach (var entry in Fixture.RootElement.GetProperty("invalid_plans").EnumerateArray())
    {
      if (entry.GetProperty("name").GetString() == name)
      {
        testCase = entry;
        break;
      }
    }

    var planJson = BuildInvalidPlan(testCase).ToJsonString();
    var expectedError = testCase.GetProperty("error").GetString()!;

    TradePlan? plan;
    try
    {
      plan = JsonSerializer.Deserialize<TradePlan>(planJson, ReadOptions);
    }
    catch (JsonException)
    {
      // A missing required field (e.g. entry.order_price: null) can fail at
      // deserialization time rather than validation time depending on the
      // record's nullability — either failure mode satisfies "rejected".
      return;
    }

    Assert.NotNull(plan);
    var ex = Assert.Throws<TradePlanContractException>(
      () => TradePlanValidator.Validate(plan!)
    );
    Assert.Contains(expectedError, ex.Message);
  }

  [Fact]
  public void MarketWatchEntryPricesAreTheZoneEdges()
  {
    var plan = JsonSerializer.Deserialize<TradePlan>(
      ValidPlan("market_watch_buy").ToJsonString(),
      ReadOptions
    )!;

    var prices = plan.Entry.EntryPrices();
    Assert.Equal(new[] { plan.Entry.ZoneLow!.Value, plan.Entry.ZoneHigh!.Value }, prices);
  }

  [Fact]
  public void MarketEntryPriceIsTheAdmittedQuoteReference()
  {
    var plan = JsonSerializer.Deserialize<TradePlan>(
      ValidPlan("market_buy_scalp_chase").ToJsonString(),
      ReadOptions
    )!;

    Assert.Equal(new[] { plan.Entry.OrderPrice!.Value }, plan.Entry.EntryPrices());
  }

  [Fact]
  public void LimitLadderEntryPricesAreTheLegPrices()
  {
    var plan = JsonSerializer.Deserialize<TradePlan>(
      ValidPlan("limit_ladder_buy").ToJsonString(),
      ReadOptions
    )!;

    var prices = plan.Entry.EntryPrices();
    Assert.Equal(plan.Entry.Legs!.Select(leg => leg.Price), prices);
  }
}
