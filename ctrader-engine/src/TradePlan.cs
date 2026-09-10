using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

// TradePlan V8 — the only trade-planning contract the executor path may
// consume. Python is the sole author of every value here; the executor
// parses and validates shape (ValidateTradePlan below) but never recomputes
// a route or a stop to compare against these values. See
// docs/adr-trade-plan-v8-cutover.md.

public static class TradePlanContract
{
  public const int Version = 8;

  public static readonly IReadOnlySet<int> SupportedVersions =
    new HashSet<int> { 8 };

  public const string EntryTypeMarketWatch = "market_watch";
  public const string EntryTypeMarket = "market";
  public const string EntryTypeSingleLimit = "single_limit";
  public const string EntryTypeLimitLadder = "limit_ladder";
  public const string EntryTypeMarketWithLimitScale = "market_with_limit_scale";

  public const string OrderTypeMarket = "market";
  public const string OrderTypeLimit = "limit";

  public static readonly IReadOnlyList<string> EntryTypes = new[]
  {
    EntryTypeMarketWatch,
    EntryTypeMarket,
    EntryTypeSingleLimit,
    EntryTypeLimitLadder,
    EntryTypeMarketWithLimitScale,
  };

  /// <summary>
  /// Whether a ladder/scale leg fires as an immediate market order rather
  /// than a resting limit - shared by TradePlanExecutionEngine's pre-submit
  /// slippage gate and TradePlanRuntime's actual submission so the two
  /// never disagree. An explicit order_type wins (market_with_limit_scale's
  /// L1 must be market); otherwise falls back to marketable-limit
  /// detection (a resting limit already priced through the live quote
  /// would fill immediately anyway).
  /// </summary>
  public static bool LegUsesMarketOrder(
    string? orderType, decimal legPrice, bool buy, decimal bid, decimal ask
  )
  {
    if (string.Equals(orderType, OrderTypeMarket, StringComparison.OrdinalIgnoreCase))
    {
      return true;
    }
    if (string.Equals(orderType, OrderTypeLimit, StringComparison.OrdinalIgnoreCase))
    {
      return false;
    }
    return buy ? legPrice >= ask : legPrice <= bid;
  }
}

public sealed class TradePlanContractException : Exception
{
  public TradePlanContractException(string message) : base(message) { }
}

public sealed record TradePlanAnalysis(
  string Strategy,
  string StrategyFamily,
  string Direction,
  IReadOnlyList<string> ContextTimeframes,
  string FormationTimeframe,
  string ConfirmationTimeframe,
  long FormationBarTs,
  long ConfirmationBarTs,
  double Score,
  int Confluence,
  string Bias,
  string Regime,
  IReadOnlyList<string>? Reasons = null,
  IReadOnlyList<string>? Tags = null,
  int? ConfluenceV1 = null,
  int? ConfluenceV2 = null,
  double? ConfluenceV2Raw = null,
  string? ConfluenceScoringVersion = null,
  // 2026-09 (owner: "collect data 2 weeks to see if order that has good
  // math quality can process well than other or not") - descriptive
  // detection-time telemetry (fib retracement ratio hit, momentum
  // velocity/acceleration, dealing-range premium/discount position),
  // never a gate. Republished onto AutoTradeEvent (Models.cs) so
  // Postgres can correlate it with the eventual fill/outcome.
  double? MathFibRatio = null,
  double? MathVelocity = null,
  double? MathAcceleration = null,
  double? MathPd = null,
  // v2 (2026-09) redefined MathAcceleration as a true per-bar second
  // derivative (was a bare velocity delta) - distinguishes legacy (null/1)
  // from v2 rows so replay/analysis never silently mixes the populations.
  int? MathFeatureVersion = null,
  // MAD v2 context telemetry - descriptive only, never a gate. Republished
  // onto AutoTradeEvent (Models.cs) the same way as the Math* fields above.
  // See algo-bot/app/analysis/mad_phase.py MadPhaseSnapshot/MadAffinityScore.
  int? MadVersion = null,
  string? MadPhase = null,
  double? MadConfidence = null,
  double? MadAffinity = null,
  string? MadDirection = null,
  string? MadSweepSide = null,
  bool? MadReclaim = null,
  double? MadRangeQualityAtr = null,
  double? MadBreakDistanceAtr = null,
  double? MadDisplacementAtr = null,
  int? MadAcceptanceCloses = null,
  double? MadSweepPenetrationAtr = null,
  double? MadReclaimDepthAtr = null,
  string? MadReasonCode = null,
  // Candle Confirmation V2 context telemetry - descriptive only, never a
  // gate. Republished onto AutoTradeEvent (Models.cs) the same way as the
  // Math*/Mad* fields above. See algo-bot/app/analysis/candle_evidence.py
  // CandleEvidence.
  int? CandleVersion = null,
  string? CandlePrimaryPattern = null,
  string? CandlePatterns = null,
  double? CandleFinalScore = null,
  double? CandleBaseScore = null,
  double? CandleSynergyBonus = null,
  double? CandleRejectionScore = null,
  double? CandleDisplacementScore = null,
  double? CandleSequenceScore = null,
  double? CandleBodyFraction = null,
  double? CandleUpperWickFraction = null,
  double? CandleLowerWickFraction = null,
  double? CandleCloseLocation = null,
  double? CandleBodyAtr = null,
  double? CandleRangeAtr = null,
  bool? CandleSweep = null,
  double? CandleSweepPenetrationAtr = null,
  bool? CandleReclaim = null,
  double? CandleReclaimDepthAtr = null,
  bool? CandleEngulfing = null,
  bool? CandleDoji = null,
  double? CandleCompressionScore = null,
  string? CandleSequenceName = null,
  int? CandleSequenceBars = null,
  // Opposing Structure V2 context telemetry (2026-09 Key Level repair) -
  // descriptive only, never a gate. Republished onto AutoTradeEvent
  // (Models.cs) the same way as the Math*/Mad*/Candle* fields above -
  // double (not decimal), matching every other field in this record,
  // including the other price/ATR-scaled ones (CandleBodyAtr etc.).
  double? KeyLevelOpposingZoneLow = null,
  double? KeyLevelOpposingZoneHigh = null,
  string? KeyLevelOpposingZoneSide = null,
  bool? OpposingZonePresent = null,
  string? OpposingZoneSide = null,
  double? OpposingZoneLow = null,
  double? OpposingZoneHigh = null,
  string? OpposingZoneTier = null,
  double? OpposingZoneScore = null,
  double? OpposingZoneStrength = null,
  double? OpposingRawRoomPrice = null,
  double? OpposingRoomPips = null,
  double? OpposingRoomAtr = null,
  double? OpposingRoomR = null,
  bool? OpposingBeforeTp1 = null,
  bool? OpposingDisplaced = null,
  bool? OpposingMitigated = null,
  double? OpposingRoomPressure = null,
  double? OpposingRiskScore = null,
  string? OpposingAction = null,
  string? OpposingReasonCode = null
);

public sealed record TradePlanSourceStructure(
  string StructureId,
  string Kind,
  string Timeframe,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal Low,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal High,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal InvalidationPrice
);

public sealed record TradePlanEntryLeg(
  string LegId,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal Price,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal VolumeRatio,
  // Optional: "market" | "limit". When set, SubmitEntryAsync places that
  // order type explicitly (market_with_limit_scale L1 must be market).
  // When null, limit_ladder keeps marketable-limit detection.
  string? OrderType = null
);

public sealed record TradePlanEntry(
  string Type,
  long ExpiresAt,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? ZoneLow = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? ZoneHigh = null,
  string? Activation = null,
  string? PriceSide = null,
  int? MaxSpreadTicks = null,
  int? MaxSlippageTicks = null,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal? OrderPrice = null,
  IReadOnlyList<TradePlanEntryLeg>? Legs = null
)
{
  // Every price at which this entry could actually fill. Used only to
  // validate the stop is on the correct side of every possible entry price
  // and that targets sit beyond the furthest entry — never to derive a new
  // price. Mirrors app/autotrade/trade_plan.py:TradePlanEntry.entry_prices.
  public IReadOnlyList<decimal> EntryPrices()
  {
    if (Type == TradePlanContract.EntryTypeMarketWatch)
    {
      if (ZoneLow is null || ZoneHigh is null)
      {
        throw new TradePlanContractException(
          "market_watch entry requires zone_low and zone_high"
        );
      }
      return new[] { ZoneLow.Value, ZoneHigh.Value };
    }
    if (
      Type is TradePlanContract.EntryTypeMarket
        or TradePlanContract.EntryTypeSingleLimit
    )
    {
      if (OrderPrice is null)
      {
        throw new TradePlanContractException(
          $"{Type} entry requires order_price"
        );
      }
      return new[] { OrderPrice.Value };
    }
    // market_with_limit_scale and limit_ladder: leg prices are references
    // (market L1 uses live quote at submit; price is for stop-side checks).
    return (Legs ?? Array.Empty<TradePlanEntryLeg>())
      .Select(leg => leg.Price)
      .ToArray();
  }
}

public sealed record TradePlanStop(
  string Type,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal Price,
  string Source,
  string? StructureId = null,
  string Reason = ""
);

public sealed record TradePlanTarget(
  string TargetId,
  string Type,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal Price,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal CloseRatio
);

public sealed record TradePlanRisk(
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal RiskPercent,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal RiskMultiplier,
  long MaxVolume,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  decimal MaxGroupRiskPercent
);

public sealed record TradePlanManagement(
  string? BeAfterTargetId,
  int BeBufferTicks,
  bool NeverWorsenStop = true,
  string? TrailAfterTargetId = null,
  string? TrailToTargetId = null
);

public sealed record TradePlanExecutionPolicy(
  bool AllowMarket = true,
  bool AllowLimit = true,
  bool AllowPartialFill = true,
  bool CancelOnExpiry = true
);

public sealed record TradePlanProvenance(
  string AnalysisEngineVersion,
  string MarketMapId,
  string ConfigFingerprint
);

public sealed record TradePlanSizing(
  string Mode,
  string TableVersion,
  string EntryDistribution,
  [property: JsonNumberHandling(JsonNumberHandling.AllowReadingFromString)]
  IReadOnlyList<decimal>? LegRatios = null
);

public sealed record TradePlan(
  int Version,
  string PlanId,
  string ThesisId,
  string SetupId,
  string Symbol,
  long CreatedAt,
  long ExpiresAt,
  TradePlanAnalysis Analysis,
  TradePlanSourceStructure SourceStructure,
  TradePlanEntry Entry,
  TradePlanStop Stop,
  IReadOnlyList<TradePlanTarget> Targets,
  TradePlanRisk Risk,
  TradePlanManagement Management,
  TradePlanExecutionPolicy ExecutionPolicy,
  TradePlanProvenance Provenance,
  TradePlanSizing? Sizing = null
);

// Execution-safety shape validation only. This is deliberately the ONLY
// place the TradePlan path inspects stop/target geometry, and it never
// re-derives what the stop or targets *should* be — it only checks that
// the values Python already declared are internally consistent (finite,
// correct side, ordered). Mirrors app/autotrade/trade_plan.py:TradePlan.validate
// exactly so the two implementations reject the same fixture cases; see
// TradePlanV8ContractTests.cs and contracts/autotrade/trade-plan-v8.json.
public static class TradePlanValidator
{
  public static void Validate(TradePlan plan)
  {
    if (!TradePlanContract.SupportedVersions.Contains(plan.Version))
    {
      throw new TradePlanContractException(
        $"unsupported TradePlan version {plan.Version}, expected one of "
        + string.Join(",", TradePlanContract.SupportedVersions)
      );
    }

    var direction = plan.Analysis.Direction;
    if (direction != "BUY" && direction != "SELL")
    {
      throw new TradePlanContractException(
        $"analysis.direction must be BUY or SELL: {direction}"
      );
    }

    var entryPrices = plan.Entry.EntryPrices();
    if (entryPrices.Count == 0)
    {
      throw new TradePlanContractException("entry has no resolvable prices");
    }

    if (direction == "BUY")
    {
      if (entryPrices.Any(price => plan.Stop.Price >= price))
      {
        throw new TradePlanContractException(
          "BUY stop.price must be below every entry price"
        );
      }
    }
    else
    {
      if (entryPrices.Any(price => plan.Stop.Price <= price))
      {
        throw new TradePlanContractException(
          "SELL stop.price must be above every entry price"
        );
      }
    }

    if (plan.Targets.Count == 0)
    {
      throw new TradePlanContractException("plan must declare at least one target");
    }

    var furthestEntry = direction == "BUY" ? entryPrices.Max() : entryPrices.Min();
    var prices = plan.Targets.Select(t => t.Price).ToArray();

    if (direction == "BUY")
    {
      if (prices.Any(price => price <= furthestEntry))
      {
        throw new TradePlanContractException(
          "BUY targets must all be above the entry zone"
        );
      }
      if (!prices.SequenceEqual(prices.OrderBy(p => p)))
      {
        throw new TradePlanContractException(
          "BUY targets must be ordered TP1 < TP2 < TP3 ..."
        );
      }
    }
    else
    {
      if (prices.Any(price => price >= furthestEntry))
      {
        throw new TradePlanContractException(
          "SELL targets must all be below the entry zone"
        );
      }
      if (!prices.SequenceEqual(prices.OrderByDescending(p => p)))
      {
        throw new TradePlanContractException(
          "SELL targets must be ordered TP1 > TP2 > TP3 ..."
        );
      }
    }

    var totalRatio = plan.Targets.Sum(t => t.CloseRatio);
    if (totalRatio > 1.0001m)
    {
      throw new TradePlanContractException(
        $"target close_ratio total exceeds 1.0: {totalRatio}"
      );
    }

    var targetIds = plan.Targets.Select(t => t.TargetId).ToList();
    if (plan.Management.BeAfterTargetId is not null)
    {
      if (!targetIds.Contains(plan.Management.BeAfterTargetId))
      {
        throw new TradePlanContractException(
          $"management.be_after_target_id '{plan.Management.BeAfterTargetId}' "
          + $"is not one of the declared targets"
        );
      }
    }

    var trailAfterId = plan.Management.TrailAfterTargetId;
    var trailToId = plan.Management.TrailToTargetId;
    if ((trailAfterId is null) != (trailToId is null))
    {
      throw new TradePlanContractException(
        "management.trail_after_target_id and trail_to_target_id "
        + "must be set together"
      );
    }
    if (trailAfterId is not null && trailToId is not null)
    {
      var trailAfterIndex = targetIds.IndexOf(trailAfterId);
      var trailToIndex = targetIds.IndexOf(trailToId);
      if (trailAfterIndex < 0)
      {
        throw new TradePlanContractException(
          $"management.trail_after_target_id '{trailAfterId}' "
          + "is not one of the declared targets"
        );
      }
      if (trailToIndex < 0)
      {
        throw new TradePlanContractException(
          $"management.trail_to_target_id '{trailToId}' "
          + "is not one of the declared targets"
        );
      }
      if (trailToIndex >= trailAfterIndex)
      {
        throw new TradePlanContractException(
          "management.trail_to_target_id must precede trail_after_target_id"
        );
      }
      if (trailAfterIndex >= targetIds.Count - 1)
      {
        throw new TradePlanContractException(
          "management.trail_after_target_id must precede the final target"
        );
      }
    }

    ValidateEntryShape(plan.Entry);
  }

  private static void ValidateEntryShape(TradePlanEntry entry)
  {
    if (!TradePlanContract.EntryTypes.Contains(entry.Type))
    {
      throw new TradePlanContractException(
        "entry.type must be one of market/market_watch/single_limit/limit_ladder/"
        + $"market_with_limit_scale: {entry.Type}"
      );
    }

    if (entry.Type == TradePlanContract.EntryTypeMarketWatch)
    {
      if (entry.ZoneLow is null || entry.ZoneHigh is null)
      {
        throw new TradePlanContractException(
          "market_watch entry requires zone_low and zone_high"
        );
      }
      if (string.IsNullOrEmpty(entry.Activation))
      {
        throw new TradePlanContractException("market_watch entry requires activation");
      }
      if (entry.PriceSide != "bid" && entry.PriceSide != "ask")
      {
        throw new TradePlanContractException(
          "market_watch entry.price_side must be 'bid' or 'ask'"
        );
      }
    }
    else if (entry.Type == TradePlanContract.EntryTypeMarket)
    {
      if (entry.OrderPrice is null)
      {
        throw new TradePlanContractException(
          "market entry requires order_price"
        );
      }
    }
    else if (entry.Type == TradePlanContract.EntryTypeSingleLimit)
    {
      if (entry.OrderPrice is null)
      {
        throw new TradePlanContractException(
          "single_limit entry requires order_price"
        );
      }
    }
    else if (
      entry.Type is TradePlanContract.EntryTypeLimitLadder
        or TradePlanContract.EntryTypeMarketWithLimitScale
    )
    {
      var legs = entry.Legs ?? Array.Empty<TradePlanEntryLeg>();
      if (legs.Count == 0)
      {
        throw new TradePlanContractException(
          $"{entry.Type} entry requires at least one leg"
        );
      }
      var totalRatio = legs.Sum(leg => leg.VolumeRatio);
      if (Math.Abs(totalRatio - 1.0m) > 0.0001m)
      {
        throw new TradePlanContractException(
          $"{entry.Type} entry.legs volume_ratio must sum to 1.0, got {totalRatio}"
        );
      }
      foreach (var leg in legs)
      {
        if (
          leg.OrderType is not null
          && leg.OrderType is not TradePlanContract.OrderTypeMarket
            and not TradePlanContract.OrderTypeLimit
        )
        {
          throw new TradePlanContractException(
            $"entry.legs[].order_type must be market or limit: {leg.OrderType}"
          );
        }
      }
      if (entry.Type == TradePlanContract.EntryTypeMarketWithLimitScale)
      {
        if (legs.Count < 2)
        {
          throw new TradePlanContractException(
            "market_with_limit_scale entry requires at least two legs"
          );
        }
        var first = legs[0].OrderType ?? TradePlanContract.OrderTypeMarket;
        var second = legs[1].OrderType ?? TradePlanContract.OrderTypeLimit;
        if (first != TradePlanContract.OrderTypeMarket)
        {
          throw new TradePlanContractException(
            "market_with_limit_scale L1 order_type must be market"
          );
        }
        if (second != TradePlanContract.OrderTypeLimit)
        {
          throw new TradePlanContractException(
            "market_with_limit_scale L2 order_type must be limit"
          );
        }
      }
    }
  }
}
