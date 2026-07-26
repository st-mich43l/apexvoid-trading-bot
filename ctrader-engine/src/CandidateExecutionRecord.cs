using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed;

public static class CandidateExecutionStates
{
  public const string Published = "published";
  public const string Processing = "processing";
  public const string BrokerSubmitting = "broker_submitting";
  public const string Ordered = "ordered";
  public const string Completed = "completed";
  public const string Rejected = "rejected";
  public const string RetryableError = "retryable_error";
  public const string BrokerOutcomeUnknown = "broker_outcome_unknown";
}

public sealed record CandidateExecutionRecord(
  string CandidateId,
  string? StreamEventId,
  string State,
  string? LeaseToken,
  long? LeaseExpiresAt,
  string? Outcome,
  long UpdatedAt,
  int Version
)
{
  public string LegacyStatus
  {
    get
    {
      if (!string.IsNullOrWhiteSpace(Outcome))
      {
        return Outcome;
      }
      return State;
    }
  }

  public bool LeaseExpired(long nowUnixSeconds) =>
    LeaseExpiresAt is long expiresAt && expiresAt <= nowUnixSeconds;

  public static CandidateExecutionRecord Published(
    string candidateId,
    string streamEventId,
    long updatedAt
  ) => new(
    candidateId,
    streamEventId,
    CandidateExecutionStates.Published,
    null,
    null,
    null,
    updatedAt,
    1
  );

  public CandidateExecutionRecord WithLease(
    string token,
    long expiresAt,
    string state,
    long updatedAt
  ) => this with
  {
    State = state,
    LeaseToken = token,
    LeaseExpiresAt = expiresAt,
    UpdatedAt = updatedAt,
  };

  public CandidateExecutionRecord WithState(
    string state,
    long updatedAt,
    string? outcome = null
  ) => this with
  {
    State = state,
    Outcome = outcome ?? Outcome,
    UpdatedAt = updatedAt,
    LeaseToken = null,
    LeaseExpiresAt = null,
  };

  public string Serialize() =>
    JsonSerializer.Serialize(this, CandidateExecutionJsonContext.Default.CandidateExecutionRecord);
}

public static class CandidateExecutionRecordParser
{
  public static CandidateExecutionRecord Parse(string raw)
  {
    if (string.IsNullOrWhiteSpace(raw))
    {
      throw new InvalidOperationException("candidate execution record is empty");
    }
    if (raw is "published" or "processing" or "dry_run")
    {
      return new CandidateExecutionRecord("", null, raw, null, null, null, 0, 1);
    }
    if (raw.StartsWith("ordered:", StringComparison.Ordinal))
    {
      return new CandidateExecutionRecord(
        "",
        null,
        CandidateExecutionStates.Ordered,
        null,
        null,
        raw,
        0,
        1
      );
    }
    if (raw.StartsWith("rejected:", StringComparison.Ordinal))
    {
      return new CandidateExecutionRecord(
        "",
        null,
        CandidateExecutionStates.Rejected,
        null,
        null,
        raw,
        0,
        1
      );
    }
    if (raw.StartsWith("flip_pending:", StringComparison.Ordinal))
    {
      return new CandidateExecutionRecord(
        "",
        null,
        CandidateExecutionStates.Completed,
        null,
        null,
        raw,
        0,
        1
      );
    }
    return JsonSerializer.Deserialize(raw, CandidateExecutionJsonContext.Default.CandidateExecutionRecord)
      ?? throw new InvalidOperationException("candidate execution record is invalid");
  }

  public static string LegacyStatus(string? raw)
  {
    if (string.IsNullOrWhiteSpace(raw))
    {
      return "";
    }
    try
    {
      return Parse(raw).LegacyStatus;
    }
    catch (InvalidOperationException)
    {
      return raw;
    }
  }

  public static bool IsCompatible(
    CandidateExecutionRecord record,
    string candidateId,
    string streamEventId
  )
  {
    if (
      !string.IsNullOrWhiteSpace(record.CandidateId)
      && !record.CandidateId.Equals(candidateId, StringComparison.Ordinal)
    )
    {
      return false;
    }
    if (
      !string.IsNullOrWhiteSpace(record.StreamEventId)
      && !string.IsNullOrWhiteSpace(streamEventId)
      && !record.StreamEventId.Equals(streamEventId, StringComparison.Ordinal)
    )
    {
      return false;
    }
    if (!string.IsNullOrWhiteSpace(record.Outcome))
    {
      return record.Outcome.StartsWith("ordered:", StringComparison.Ordinal)
        || record.Outcome.StartsWith("rejected:", StringComparison.Ordinal)
        || record.Outcome.StartsWith("flip_pending:", StringComparison.Ordinal);
    }
    return record.State is CandidateExecutionStates.Published
      or CandidateExecutionStates.Processing
      or CandidateExecutionStates.BrokerSubmitting
      or CandidateExecutionStates.Ordered
      or CandidateExecutionStates.Completed
      or CandidateExecutionStates.Rejected
      or CandidateExecutionStates.RetryableError
      or CandidateExecutionStates.BrokerOutcomeUnknown
      or "dry_run";
  }

  public static string OutcomeState(string outcome)
  {
    if (outcome.StartsWith("ordered:", StringComparison.Ordinal))
    {
      return CandidateExecutionStates.Ordered;
    }
    if (outcome.StartsWith("rejected:", StringComparison.Ordinal))
    {
      return CandidateExecutionStates.Rejected;
    }
    if (outcome.StartsWith("flip_pending:", StringComparison.Ordinal))
    {
      return CandidateExecutionStates.Completed;
    }
    return CandidateExecutionStates.Completed;
  }
}

[JsonSourceGenerationOptions(
  DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
  PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower
)]
[JsonSerializable(typeof(CandidateExecutionRecord))]
internal sealed partial class CandidateExecutionJsonContext : JsonSerializerContext;
