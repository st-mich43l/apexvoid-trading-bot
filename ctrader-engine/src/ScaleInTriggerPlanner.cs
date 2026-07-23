namespace ApexVoid.CTraderFeed;

public sealed record ScaleInTriggerInput(
  TradeDirection GroupDirection,
  TradeDirection CandidateDirection,
  decimal InitialEntry,
  decimal AddEntry,
  decimal GroupPnl,
  bool InitialReachedBreakeven,
  bool EveryStopAvailable,
  bool OneGroup,
  int LifetimeTrancheCount,
  int MaximumTranches,
  string? DisplacementDirection,
  int? DisplacementAgeBars,
  int MaximumDisplacementAgeBars,
  string? BosDirection,
  long? BosTimestamp,
  long GroupOpenedAt,
  decimal? OpposingLevelDistanceAtr,
  decimal OpposingLevelBufferAtr,
  long BarTimestamp,
  long PreviousTrancheBarTimestamp,
  int CooldownBars,
  // Pullback add (P1-P4) - all ignored unless PullbackEnabled and momentum's
  // own displacement check has already failed to qualify (see Validate).
  bool PullbackEnabled = false,
  bool CounterBosSinceGroupOpen = false,
  decimal? ExtremeSinceGroupOpen = null,
  decimal MinRetraceRatio = 0.20m,
  decimal MaxRetraceRatio = 0.70m,
  decimal? AddZoneLow = null,
  decimal? AddZoneHigh = null,
  string? AddZoneSide = null,
  bool RejectionConfirmed = false
);

// "shared" - a candidate direction/tranche-count/breakeven/loss-ceiling/
// averaging-down invariant failed before mode selection even ran, so
// attributing it to either mode specifically would be misleading.
public sealed record ScaleInTriggerResult(
  bool Accepted,
  string? Mode,
  string? RejectReason,
  string? Condition
)
{
  public static ScaleInTriggerResult Accept(string mode) => new(true, mode, null, null);

  public static ScaleInTriggerResult Reject(
    string mode,
    string condition,
    string reason
  ) => new(false, mode, reason, condition);
}

public static class ScaleInTriggerPlanner
{
  public static ScaleInTriggerResult Validate(ScaleInTriggerInput input)
  {
    if (!input.OneGroup || input.GroupDirection != input.CandidateDirection)
    {
      return ScaleInTriggerResult.Reject(
        "shared",
        "direction_mismatch",
        "candidate direction does not match the open tranche group"
      );
    }
    if (input.LifetimeTrancheCount >= input.MaximumTranches)
    {
      return ScaleInTriggerResult.Reject(
        "shared",
        "max_tranches",
        $"maximum tranche count {input.MaximumTranches} reached"
      );
    }
    if (!input.InitialReachedBreakeven)
    {
      return ScaleInTriggerResult.Reject(
        "shared",
        "not_breakeven",
        "initial tranche has not reached TP1/breakeven"
      );
    }
    if (!input.EveryStopAvailable)
    {
      return ScaleInTriggerResult.Reject(
        "shared",
        "stop_unavailable",
        "group loss ceiling unavailable: tranche stop is missing"
      );
    }
    var favorableEntry = input.CandidateDirection == TradeDirection.Buy
      ? input.AddEntry > input.InitialEntry
      : input.AddEntry < input.InitialEntry;
    if (input.GroupPnl <= 0 || !favorableEntry)
    {
      return ScaleInTriggerResult.Reject(
        "shared",
        "averaging_down_guard",
        "averaging-down guard: add requires a profitable group and a "
          + "strictly favorable entry"
      );
    }
    var direction = input.CandidateDirection == TradeDirection.Buy
      ? "up"
      : "down";

    // ---- Momentum add (unchanged from the original single-mode Validate) ----
    var momentumDisplacementOk =
      string.Equals(
        input.DisplacementDirection,
        direction,
        StringComparison.OrdinalIgnoreCase
      )
      && input.DisplacementAgeBars is int displacementAge
      && displacementAge >= 0
      && displacementAge <= input.MaximumDisplacementAgeBars;
    // A candidate never satisfies both modes: fresh in-direction displacement
    // is evaluated purely as a momentum add regardless of anything else,
    // even if it then fails a later momentum-only condition below.
    if (momentumDisplacementOk)
    {
      if (
        !string.Equals(
          input.BosDirection,
          direction,
          StringComparison.OrdinalIgnoreCase
        )
        || input.BosTimestamp is not long bosTimestamp
        || bosTimestamp < input.GroupOpenedAt
      )
      {
        return ScaleInTriggerResult.Reject(
          "add_momentum",
          "bos_missing",
          $"momentum add requires {direction} BOS since initial entry"
        );
      }
      if (
        input.OpposingLevelDistanceAtr is decimal opposingDistance
        && opposingDistance <= input.OpposingLevelBufferAtr
      )
      {
        return ScaleInTriggerResult.Reject(
          "add_momentum",
          "opposing_level_buffer",
          $"opposing level {opposingDistance:N2} ATR ahead is inside "
            + $"{input.OpposingLevelBufferAtr:N2} ATR buffer"
        );
      }
      var momentumCooldownFailure = CooldownFailure(input);
      if (momentumCooldownFailure is not null)
      {
        return ScaleInTriggerResult.Reject(
          "add_momentum",
          "cooldown",
          momentumCooldownFailure
        );
      }
      return ScaleInTriggerResult.Accept("add_momentum");
    }

    var momentumRejection = $"momentum add requires fresh {direction} displacement "
      + $"within {input.MaximumDisplacementAgeBars} bars";
    if (!input.PullbackEnabled)
    {
      return ScaleInTriggerResult.Reject(
        "add_momentum",
        "displacement_stale_or_wrong_direction",
        momentumRejection
      );
    }

    // ---- Pullback add (P1-P4; P5/P6 are evaluated by the caller once it
    // has ATR/zone/sizing context this planner deliberately doesn't take) ----
    if (input.CounterBosSinceGroupOpen)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "counter_bos",
        "pullback add rejected: counter BOS since group open"
      );
    }
    if (input.ExtremeSinceGroupOpen is not decimal extreme)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "no_extreme_history",
        "pullback add rejected: no price extreme available since group open"
      );
    }
    var retraceDenominator = Math.Abs(input.InitialEntry - extreme);
    if (retraceDenominator <= 0)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "no_extreme_history",
        "pullback add rejected: initial entry and extreme coincide"
      );
    }
    var retraceRatio = Math.Abs(input.AddEntry - extreme) / retraceDenominator;
    if (retraceRatio < input.MinRetraceRatio)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "retrace_below_min",
        $"pullback add rejected: retrace {retraceRatio:0.00} below min "
          + $"{input.MinRetraceRatio:0.00}"
      );
    }
    if (retraceRatio > input.MaxRetraceRatio)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "retrace_above_max",
        $"pullback add rejected: retrace {retraceRatio:0.00} exceeds max "
          + $"{input.MaxRetraceRatio:0.00}"
      );
    }
    var expectedZoneSide = input.CandidateDirection == TradeDirection.Buy
      ? "demand"
      : "supply";
    if (
      input.AddZoneLow is not decimal addZoneLow
      || input.AddZoneHigh is not decimal addZoneHigh
      || !string.Equals(
        input.AddZoneSide,
        expectedZoneSide,
        StringComparison.OrdinalIgnoreCase
      )
      || input.AddEntry < addZoneLow
      || input.AddEntry > addZoneHigh
    )
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "zone_missing_or_wrong_side",
        $"pullback add rejected: no mapped {expectedZoneSide} zone at "
          + $"{input.AddEntry:0.##}"
      );
    }
    if (!input.RejectionConfirmed)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "rejection_not_confirmed",
        "pullback add rejected: no M1 rejection candle confirming the retrace"
      );
    }
    var pullbackCooldownFailure = CooldownFailure(input);
    if (pullbackCooldownFailure is not null)
    {
      return ScaleInTriggerResult.Reject(
        "add_pullback",
        "cooldown",
        pullbackCooldownFailure
      );
    }
    return ScaleInTriggerResult.Accept("add_pullback");
  }

  private static string? CooldownFailure(ScaleInTriggerInput input)
  {
    var minimumSeconds = input.CooldownBars * 60L;
    if (
      input.BarTimestamp <= 0
      || input.BarTimestamp - input.PreviousTrancheBarTimestamp < minimumSeconds
    )
    {
      return $"tranche cooldown {input.CooldownBars} bars not elapsed";
    }
    return null;
  }
}
