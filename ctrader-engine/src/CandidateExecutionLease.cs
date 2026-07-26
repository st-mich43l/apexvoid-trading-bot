namespace ApexVoid.CTraderFeed;

public sealed record CandidateExecutionLease(
  string CandidateId,
  string StreamEventId,
  string Token,
  DateTimeOffset ExpiresAt
);
