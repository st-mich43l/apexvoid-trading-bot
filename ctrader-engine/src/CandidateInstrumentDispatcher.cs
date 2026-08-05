namespace ApexVoid.CTraderFeed;

/// <summary>
/// Routes shared-stream candidates to an instrument execution runtime.
/// Rejects unknown / non-executable / paper→live mismatches fail-closed.
/// </summary>
public enum CandidateRouteOutcome
{
  AcceptLive,
  AcceptPaper,
  RejectMissingSymbol,
  RejectUnknownSymbol,
  RejectDisabled,
  RejectFeedOnly,
  RejectAnalysisOnly,
  RejectPaperToLiveBroker,
  RejectNonLiveExecution,
  RejectAliasNotNormalized,
  RejectSymbolRuntimeMismatch,
}

public sealed record CandidateRouteDecision(
  CandidateRouteOutcome Outcome,
  InstrumentRuntime? Runtime,
  string Reason
)
{
  public bool IsAccepted =>
    Outcome is CandidateRouteOutcome.AcceptLive or CandidateRouteOutcome.AcceptPaper;
}

public static class CandidateInstrumentDispatcher
{
  public static CandidateRouteDecision Route(
    InstrumentRuntimeRegistry registry,
    string? candidateSymbol,
    bool paperCandidate = false
  )
  {
    if (string.IsNullOrWhiteSpace(candidateSymbol))
    {
      return new CandidateRouteDecision(
        CandidateRouteOutcome.RejectMissingSymbol,
        null,
        "candidate.symbol is missing"
      );
    }
    var raw = candidateSymbol.Trim();
    if (!registry.TryGet(raw, out var runtime))
    {
      return new CandidateRouteDecision(
        CandidateRouteOutcome.RejectUnknownSymbol,
        null,
        $"unknown candidate symbol {raw}"
      );
    }
    var canonical = runtime.Feed.CanonicalSymbol;
    if (
      !raw.Equals(canonical, StringComparison.OrdinalIgnoreCase)
      && !raw.Equals(runtime.InstrumentId, StringComparison.OrdinalIgnoreCase)
    )
    {
      // Alias forms are accepted only after normalization to canonical.
      // Callers must publish canonical symbols on the shared stream.
      return new CandidateRouteDecision(
        CandidateRouteOutcome.RejectAliasNotNormalized,
        runtime,
        $"candidate symbol {raw} must be canonical {canonical}"
      );
    }
    return runtime.Rollout switch
    {
      InstrumentRollout.Disabled => new CandidateRouteDecision(
        CandidateRouteOutcome.RejectDisabled,
        runtime,
        $"instrument {runtime.InstrumentId} rollout=disabled"
      ),
      InstrumentRollout.FeedOnly => new CandidateRouteDecision(
        CandidateRouteOutcome.RejectFeedOnly,
        runtime,
        $"instrument {runtime.InstrumentId} rollout=feed_only"
      ),
      InstrumentRollout.AnalysisOnly => new CandidateRouteDecision(
        CandidateRouteOutcome.RejectAnalysisOnly,
        runtime,
        $"instrument {runtime.InstrumentId} rollout=analysis_only"
      ),
      InstrumentRollout.Paper when !paperCandidate => new CandidateRouteDecision(
        CandidateRouteOutcome.RejectPaperToLiveBroker,
        runtime,
        $"instrument {runtime.InstrumentId} is paper; live candidate rejected"
      ),
      InstrumentRollout.Paper => new CandidateRouteDecision(
        CandidateRouteOutcome.AcceptPaper,
        runtime,
        "paper route accepted (no broker placement)"
      ),
      InstrumentRollout.Live when paperCandidate => new CandidateRouteDecision(
        CandidateRouteOutcome.RejectNonLiveExecution,
        runtime,
        $"paper candidate cannot execute on live instrument {runtime.InstrumentId}"
      ),
      InstrumentRollout.Live => new CandidateRouteDecision(
        CandidateRouteOutcome.AcceptLive,
        runtime,
        "live route accepted"
      ),
      _ => new CandidateRouteDecision(
        CandidateRouteOutcome.RejectUnknownSymbol,
        runtime,
        $"unsupported rollout for {runtime.InstrumentId}"
      ),
    };
  }
}
