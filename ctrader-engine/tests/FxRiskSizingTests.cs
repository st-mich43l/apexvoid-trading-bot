using Xunit;

namespace ApexVoid.CTraderFeed.Tests;

// FX1: an FX plan whose sizing mode is "risk" (FX 2% of equity) is sized lots = equity x risk% / (stop pips x pip value per lot), floored to the broker step, so the realised
// risk is 2% (never above, at most one lot step below). The pip value of a JPY-quoted pair comes from the live USDJPY quote, not from a constant that drifts.
public class FxRiskSizingTests
{
  // RedisSymbol, CTraderSymbol, SymbolId, Digits, PipPosition, MinVolume, StepVolume, MaxVolume, LotSize: FX volume is 10,000,000 per lot, 0.01 lot = 100,000.
  private static SymbolInfo Fx(string symbol, int digits) => new(symbol, symbol, 2, digits, digits - 1, 100_000, 100_000, 1_000_000_000, 10_000_000);

  private static string Plan(string symbol, string direction, decimal entry, decimal stopPips, decimal pipSize, string sizingMode)
  {
    var buy = direction == "BUY";
    var stop = buy ? entry - stopPips * pipSize : entry + stopPips * pipSize;
    var target = buy ? entry + 2 * stopPips * pipSize : entry - 2 * stopPips * pipSize;
    var json = """
    {"version":8,"plan_id":"v8:fx1","thesis_id":"t","setup_id":"s","symbol":"@SYM","created_at":1719999600,"expires_at":2000000000,
     "analysis":{"strategy":"Key Level","strategy_family":"key_level","direction":"@DIR","context_timeframes":["M15"],"formation_timeframe":"M5","confirmation_timeframe":"M5",
       "formation_bar_ts":1719999300,"confirmation_bar_ts":1719999600,"score":3.0,"confluence":3,"bias":"up","regime":"trend","reasons":[],"tags":[]},
     "source_structure":{"structure_id":"keylevel:round:1","kind":"key_level","timeframe":"M5","low":"@LOW","high":"@HIGH","invalidation_price":"@STOP"},
     "entry":{"type":"single_limit","expires_at":2000000000,"order_price":"@PRICE","legs":[]},
     "stop":{"type":"absolute","price":"@STOP","source":"structure","structure_id":"keylevel:round:1","reason":"kl"},
     "targets":[{"target_id":"TP1","type":"absolute","price":"@TARGET","close_ratio":"1.0"}],
     "risk":{"risk_percent":"2.0","risk_multiplier":"1.5","max_volume":100000000,"max_group_risk_percent":"2.0"},
     "sizing":{"mode":"@MODE","table_version":"owner_equity_v1","entry_distribution":"single","leg_ratios":[]},
     "management":{"be_after_target_id":null,"be_buffer_ticks":6,"never_worsen_stop":true},
     "execution_policy":{"allow_market":false,"allow_limit":true,"allow_partial_fill":true,"cancel_on_expiry":true},
     "provenance":{"analysis_engine_version":"fx1","market_map_id":"","config_fingerprint":""}}
    """;
    return json.Replace("@SYM", symbol).Replace("@DIR", direction).Replace("@PRICE", entry.ToString()).Replace("@STOP", stop.ToString()).Replace("@TARGET", target.ToString())
      .Replace("@LOW", (entry - 0.0002m).ToString()).Replace("@HIGH", (entry + 0.0002m).ToString()).Replace("@MODE", sizingMode);
  }

  private static TradingAccountSnapshot Account(decimal equity) => new(1, false, "trade", "full", "demo", "test", equity, equity, 0, "test");

  private static decimal RealisedRiskPercent(SymbolInfo symbol, long volume, decimal stopPips, decimal pipValuePerLot, decimal equity) =>
    (decimal)volume / symbol.LotSize * stopPips * pipValuePerLot / equity * 100m;

  public static IEnumerable<object[]> Cases()
  {
    // symbol, entry price, pip size, digits, pip value per lot (USD; JPY pairs at USDJPY 159.2 -> 1000/159.2)
    var pairs = new (string, decimal, decimal, int, decimal)[]
    {
      ("EURUSD", 1.0850m, 0.0001m, 5, 10m),
      ("GBPUSD", 1.3000m, 0.0001m, 5, 10m),
      ("USDJPY", 159.20m, 0.01m, 3, 6.2814m),
      ("GBPJPY", 211.60m, 0.01m, 3, 6.2814m),
    };
    foreach (var (symbol, entry, pip, digits, value) in pairs)
    {
      foreach (var equity in new[] { 600m, 708m, 1000m, 1500m, 3000m })
      {
        foreach (var stop in new[] { 12m, 15m, 18m, 22m })
        {
          yield return [symbol, entry, pip, digits, value, equity, stop];
        }
      }
    }
  }

  [Theory]
  [MemberData(nameof(Cases))]
  public void RiskSizedFxLotsRiskTwoPercentNeverMoreAndAtMostOneStepLess(string symbolName, decimal entry, decimal pip, int digits, decimal pipValue, decimal equity, decimal stopPips)
  {
    var symbol = Fx(symbolName, digits);
    var plan = TradePlanJson.DeserializePlan(Plan(symbolName, "BUY", entry, stopPips, pip, "risk"));
    var volume = TradePlanExecutionEngine.CalculateVolume(plan, Account(equity), pip, pipValue, symbol).TotalVolume;
    var risk = RealisedRiskPercent(symbol, volume, stopPips, pipValue, equity);
    var oneStep = RealisedRiskPercent(symbol, symbol.StepVolume, stopPips, pipValue, equity);
    Assert.True(risk <= 2.0m + 0.0001m, $"{symbolName} equity {equity} stop {stopPips}: realised {risk:0.000}% is above 2%");
    Assert.True(risk >= 2.0m - oneStep - 0.0001m, $"{symbolName} equity {equity} stop {stopPips}: realised {risk:0.000}% is more than one lot step ({oneStep:0.000}%) below 2%");
    if (oneStep <= 0.2m)
    {
      Assert.InRange(risk, 1.8m, 2.1m); // the FX1 rule wherever one lot step is small enough to hit it
    }
  }

  [Fact]
  public void TheEquityTableLaneRisksMoreThanTwoPercentOnceEquityFallsBelowAThousand()
  {
    // The bug FX1 fixes: with sizing.mode equity_table the executor takes the table lots x risk_multiplier 1.5 whatever the stop, so the risk as a share of equity RISES as the
    // account falls: 0.18 lots at $1,000-$2,000, 0.15 lots at $600-$1,000 (about 2.4% on a $708 account with an 18-pip USDJPY stop), more below.
    var symbol = Fx("USDJPY", 3);
    var plan = TradePlanJson.DeserializePlan(Plan("USDJPY", "BUY", 159.20m, 18m, 0.01m, "equity_table"));
    var atEquity = (decimal equity) => TradePlanExecutionEngine.CalculateVolume(plan, Account(equity), 0.01m, 6.2814m, symbol).TotalVolume;
    Assert.Equal(1_500_000, atEquity(708m)); // 0.15 lots
    Assert.InRange(RealisedRiskPercent(symbol, atEquity(708m), 18m, 6.2814m, 708m), 2.3m, 2.5m);
    Assert.Equal(1_800_000, atEquity(1500m)); // 0.18 lots
    Assert.True(RealisedRiskPercent(symbol, atEquity(600m), 18m, 6.2814m, 600m) > 2.7m);
  }

  [Fact]
  public void JpyQuotedPipValueFollowsTheLiveUsdJpyRate()
  {
    Assert.Equal(6.2814m, PipValue.JpyQuotedUsdPerLot(0.01m, 100_000m, 159.19m, 159.21m));
    Assert.Equal(6.9999m, PipValue.JpyQuotedUsdPerLot(0.01m, 100_000m, 142.85m, 142.87m)); // the rate GBPJPY's hardcoded 7.0 was set at
    Assert.True(PipValue.IsJpyQuoted("GBPJPY") && PipValue.IsJpyQuoted("usdjpy"));
    Assert.False(PipValue.IsJpyQuoted("EURUSD") || PipValue.IsJpyQuoted("XAU") || PipValue.IsJpyQuoted(""));
  }

  [Theory]
  [InlineData(0.0, 0.0)]
  [InlineData(159.2, 159.1)] // crossed
  [InlineData(10.0, 10.1)] // not a USDJPY rate
  [InlineData(900.0, 901.0)]
  public void AnUnusableUsdJpyQuoteKeepsTheConfiguredValue(double bid, double ask) =>
    Assert.Null(PipValue.JpyQuotedUsdPerLot(0.01m, 100_000m, (decimal)bid, (decimal)ask));
}
