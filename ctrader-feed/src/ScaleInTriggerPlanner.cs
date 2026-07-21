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
  int CooldownBars
);

public static class ScaleInTriggerPlanner
{
  public static string? Validate(ScaleInTriggerInput input)
  {
    if (!input.OneGroup || input.GroupDirection != input.CandidateDirection)
    {
      return "candidate direction does not match the open tranche group";
    }
    if (input.LifetimeTrancheCount >= input.MaximumTranches)
    {
      return $"maximum tranche count {input.MaximumTranches} reached";
    }
    if (!input.InitialReachedBreakeven)
    {
      return "initial tranche has not reached TP1/breakeven";
    }
    if (!input.EveryStopAvailable)
    {
      return "group loss ceiling unavailable: tranche stop is missing";
    }
    var favorableEntry = input.CandidateDirection == TradeDirection.Buy
      ? input.AddEntry > input.InitialEntry
      : input.AddEntry < input.InitialEntry;
    if (input.GroupPnl <= 0 || !favorableEntry)
    {
      return "averaging-down guard: add requires a profitable group and a "
        + "strictly favorable entry";
    }
    var direction = input.CandidateDirection == TradeDirection.Buy
      ? "up"
      : "down";
    if (
      !string.Equals(
        input.DisplacementDirection,
        direction,
        StringComparison.OrdinalIgnoreCase
      )
      || input.DisplacementAgeBars is not int displacementAge
      || displacementAge < 0
      || displacementAge > input.MaximumDisplacementAgeBars
    )
    {
      return $"momentum add requires fresh {direction} displacement within "
        + $"{input.MaximumDisplacementAgeBars} bars";
    }
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
      return $"momentum add requires {direction} BOS since initial entry";
    }
    if (
      input.OpposingLevelDistanceAtr is decimal opposingDistance
      && opposingDistance <= input.OpposingLevelBufferAtr
    )
    {
      return $"opposing level {opposingDistance:N2} ATR ahead is inside "
        + $"{input.OpposingLevelBufferAtr:N2} ATR buffer";
    }
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
