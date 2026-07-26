namespace ApexVoid.CTraderFeed;

public sealed record CandidateExecutionLease(
  string CandidateId,
  string StreamEventId,
  string Token,
  DateTimeOffset ExpiresAt
)
{
  // Lease tokens are secrets that authorise broker mutations, so logs only
  // ever carry a short correlation prefix.
  public string Correlation => Token.Length <= 8
    ? Token
    : Token[..8];
}

public static class CandidateLeaseDefaults
{
  public static readonly TimeSpan LeaseWindow = TimeSpan.FromMinutes(2);

  // Well below a third of the lease so one delayed scheduling slice cannot
  // expire ownership mid-broker-call.
  public static readonly TimeSpan HeartbeatInterval = TimeSpan.FromSeconds(30);
}
