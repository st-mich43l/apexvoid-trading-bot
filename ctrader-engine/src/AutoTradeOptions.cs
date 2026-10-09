namespace ApexVoid.CTraderFeed;

/// <summary>
/// Settings the TradePlan V8 executor reads. Everything about how a plan is
/// built (entry, stop, targets, risk) lives in the plan itself.
/// </summary>
public sealed record AutoTradeOptions(
  bool Enabled,
  bool DryRun,
  string ExpectedBroker,
  int PollMilliseconds,
  string EventStream,
  string Label,
  bool RequireDemoOnlyToken = false,
  decimal RiskPercent = 2m,
  string SizingMode = "min",
  decimal PipValuePerLot = 10m,
  decimal PipSize = 0.1m,
  decimal ContractSize = 100m,
  string Profile = "conservative",
  bool RequireDemoAccount = true,
  string RedisUrl = "redis://redis:6379/0",
  string CanonicalSymbol = "XAU",
  string TradePlanStream = "execution:trade_plans",
  IReadOnlyList<string>? Symbols = null,
  string EquityTableVersion = "owner_equity_v1",
  // cancel = cancel remaining pending entry legs before TP1/BE/trail/manual
  // close/terminal invalidation. keep = leave them resting.
  string UnfilledLegAfterTpPolicy = "cancel",
  // market_with_limit_scale: single_market collapses to 100% L1 market when
  // two valid legs cannot be formed; reject refuses the plan.
  string ReactionScaleInvalidPolicy = "single_market",
  // A live quote older than this opens no new entry (positions already open, resting
  // orders and broker-side stops are untouched). 0 disables the check (record default,
  // used by tests); the native runtime derives it from analysis.spot.maximum_age_seconds.
  int MaximumQuoteAgeSeconds = 0
)
{
  public IReadOnlyList<string> EffectiveSymbols =>
    (Symbols ?? [CanonicalSymbol])
      .Select(value => value.Trim().ToUpperInvariant())
      .Where(value => value.Length > 0)
      .Distinct(StringComparer.Ordinal)
      .Order(StringComparer.Ordinal)
      .ToArray();

  public void Validate()
  {
    if (Profile is not "conservative" and not "demo_eval")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_PROFILE must be conservative or demo_eval"
      );
    }
    if (Profile == "demo_eval" && !RequireDemoAccount)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: demo_eval requires AUTO_TRADE_REQUIRE_DEMO_ACCOUNT=true"
      );
    }
    if (string.IsNullOrWhiteSpace(CanonicalSymbol) || EffectiveSymbols.Count == 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: symbols and canonical symbol must be configured"
      );
    }
    if (RiskPercent is < 0.1m or > 10m || PipValuePerLot <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: risk percent must be 0.1-10 and pip value positive"
      );
    }
    if (SizingMode is not "min" and not "table" and not "risk" and not "equity_table")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_SIZING_MODE must be one of "
        + "min, table, risk, equity_table"
      );
    }
    if (string.IsNullOrWhiteSpace(EquityTableVersion))
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_EQUITY_TABLE_VERSION must be set"
      );
    }
    if (UnfilledLegAfterTpPolicy is not "cancel" and not "keep")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY "
        + "must be cancel or keep"
      );
    }
    if (MaximumQuoteAgeSeconds < 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: maximum quote age must not be negative"
      );
    }
    if (ReactionScaleInvalidPolicy is not "single_market" and not "reject")
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_REACTION_SCALE_INVALID_POLICY "
        + "must be single_market or reject"
      );
    }
    if (PipSize <= 0 || ContractSize <= 0)
    {
      throw new AutoTradeConfigurationException(
        "Auto trade disabled: AUTO_TRADE_XAU_PIP_SIZE and "
        + "AUTO_TRADE_XAU_CONTRACT_SIZE must be positive"
      );
    }
    var derivedPipValue = ContractSize * PipSize;
    if (PipValuePerLot != derivedPipValue)
    {
      throw new AutoTradeConfigurationException(
        $"Auto trade disabled: pip value inconsistent: PipValuePerLot="
        + $"{PipValuePerLot} but ContractSize {ContractSize} x PipSize "
        + $"{PipSize} = {derivedPipValue}"
      );
    }
  }
}
