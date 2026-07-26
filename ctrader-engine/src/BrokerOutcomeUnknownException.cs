namespace ApexVoid.CTraderFeed;

// Raised when a broker request may have been accepted but the executor never
// received an authoritative answer. This is distinct from a confirmed
// rejection and from a pre-submit failure: a blind retry could duplicate a
// live order, so the candidate must stay in `broker_outcome_unknown` until
// deterministic reconciliation adopts or disproves the order.
public sealed class BrokerOutcomeUnknownException : Exception
{
  public BrokerOutcomeUnknownException(
    string candidateId,
    string clientOrderId,
    Exception? inner = null,
    bool leaseLostAfterBroker = false
  ) : base("broker outcome is unknown", inner)
  {
    CandidateId = candidateId;
    ClientOrderId = clientOrderId;
    LeaseLostAfterBroker = leaseLostAfterBroker;
  }

  public string CandidateId { get; }

  public string ClientOrderId { get; }

  // True only when the candidate lease was lost after a broker request may
  // have begun. Distinct from a transport-level acknowledgement loss while
  // the executor still owns the lease.
  public bool LeaseLostAfterBroker { get; }
}

// Raised when a candidate execution record violates identity invariants (for
// example a foreign candidate ID or an unparseable record). Intake stops
// rather than guessing whether the candidate is retryable.
public sealed class CandidateIntegrityException : Exception
{
  public CandidateIntegrityException(string candidateId, string reason)
    : base($"candidate {candidateId} integrity error: {reason}")
  {
    CandidateId = candidateId;
    Reason = reason;
  }

  public string CandidateId { get; }

  public string Reason { get; }
}

// How a candidate's broker interaction ended. Only `Rejected` is terminal and
// cursor-advancing; `Unknown` always routes into reconciliation.
public enum BrokerSideEffectOutcome
{
  NotAttempted,
  Rejected,
  Accepted,
  Unknown,
}

// Typed result of deterministic broker reconciliation for a recovery-required
// candidate. A single empty snapshot is never ConfirmedAbsent.
public enum BrokerRecoveryDisposition
{
  AdoptedPosition,
  AdoptedPendingOrder,
  ConfirmedAbsent,
  StillUnknown,
  Conflict,
}
