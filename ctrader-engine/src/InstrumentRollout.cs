namespace ApexVoid.CTraderFeed;

/// <summary>
/// Instrument rollout policy. Paper is not silently mapped to global dry-run.
/// </summary>
public enum InstrumentRollout
{
  Disabled,
  FeedOnly,
  AnalysisOnly,
  Paper,
  Live,
}

public static class InstrumentRolloutGates
{
  public static bool PermitsFeed(InstrumentRollout rollout) =>
    rollout != InstrumentRollout.Disabled;

  public static bool PermitsAnalysis(InstrumentRollout rollout) =>
    rollout is InstrumentRollout.AnalysisOnly
      or InstrumentRollout.Paper
      or InstrumentRollout.Live;

  public static bool PermitsCandidatePublication(InstrumentRollout rollout) =>
    rollout is InstrumentRollout.Paper or InstrumentRollout.Live;

  public static bool PermitsBrokerExecution(InstrumentRollout rollout) =>
    rollout == InstrumentRollout.Live;

  public static InstrumentRollout Parse(string? value)
  {
    if (string.IsNullOrWhiteSpace(value))
    {
      throw new InvalidOperationException("instrument rollout is required");
    }
    return value.Trim().ToLowerInvariant() switch
    {
      "disabled" => InstrumentRollout.Disabled,
      "feed_only" => InstrumentRollout.FeedOnly,
      "analysis_only" => InstrumentRollout.AnalysisOnly,
      "paper" => InstrumentRollout.Paper,
      "live" => InstrumentRollout.Live,
      _ => throw new InvalidOperationException(
        $"unsupported instrument rollout '{value}'"
      ),
    };
  }

  public static string ToWire(InstrumentRollout rollout) =>
    rollout switch
    {
      InstrumentRollout.Disabled => "disabled",
      InstrumentRollout.FeedOnly => "feed_only",
      InstrumentRollout.AnalysisOnly => "analysis_only",
      InstrumentRollout.Paper => "paper",
      InstrumentRollout.Live => "live",
      _ => throw new InvalidOperationException($"unknown rollout {rollout}"),
    };
}
