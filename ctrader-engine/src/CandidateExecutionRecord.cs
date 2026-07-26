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
  public const string DryRun = "dry_run";
  public const string RetryableError = "retryable_error";
  public const string BrokerOutcomeUnknown = "broker_outcome_unknown";
  // Recovery-active ownership. Structurally separate from `processing` so a
  // crashed recovery worker can never decay into a normal retry.
  public const string BrokerReconciling = "broker_reconciling";

  // Explicit categories. Retryability is never inferred from an arbitrary
  // string: an unrecognised state is a conflict, not a retry.
  public static readonly IReadOnlySet<string> ActiveLeaseOwned =
    new HashSet<string>(StringComparer.Ordinal) { Processing, BrokerSubmitting };

  public static readonly IReadOnlySet<string> Retryable =
    new HashSet<string>(StringComparer.Ordinal) { Published, RetryableError };

  public static readonly IReadOnlySet<string> RecoveryRequired =
    new HashSet<string>(StringComparer.Ordinal)
    {
      BrokerOutcomeUnknown,
      BrokerReconciling,
    };

  public static readonly IReadOnlySet<string> Terminal =
    new HashSet<string>(StringComparer.Ordinal)
    {
      Ordered,
      Completed,
      Rejected,
      DryRun,
    };

  private static readonly Dictionary<string, IReadOnlySet<string>> AllowedTransitions =
    new(StringComparer.Ordinal)
    {
      [Processing] = new HashSet<string>(StringComparer.Ordinal)
      {
        BrokerSubmitting,
        BrokerOutcomeUnknown,
        RetryableError,
      },
      [BrokerSubmitting] = new HashSet<string>(StringComparer.Ordinal)
      {
        BrokerOutcomeUnknown,
      },
      // Only the recovery-active state may hand a candidate back for a normal
      // retry, and only after broker absence quorum. `broker_outcome_unknown`
      // itself must first be claimed by the recovery path.
      [BrokerReconciling] = new HashSet<string>(StringComparer.Ordinal)
      {
        BrokerOutcomeUnknown,
        RetryableError,
      },
    };

  public static bool IsTerminal(string? state) =>
    state is not null && Terminal.Contains(state);

  public static bool IsRetryable(string? state) =>
    state is not null && Retryable.Contains(state);

  public static bool IsActiveLeaseOwned(string? state) =>
    state is not null && ActiveLeaseOwned.Contains(state);

  public static bool IsRecoveryRequired(string? state) =>
    state is not null && RecoveryRequired.Contains(state);

  public static bool IsKnown(string? state) =>
    IsTerminal(state)
    || IsRetryable(state)
    || IsActiveLeaseOwned(state)
    || IsRecoveryRequired(state);

  public static bool CanTransition(string? from, string to) =>
    from is not null
    && AllowedTransitions.TryGetValue(from, out var allowed)
    && allowed.Contains(to);
}

// Explicit, per-call-site claim policy. Nothing widens claimability by
// accident: each relaxation names the invariant that makes it safe.
public sealed record CandidateClaimPolicy(
  // Flip rendezvous keys re-arm after a rejected claim.
  bool AllowRejectedReclaim = false,
  // Expired `broker_submitting` is recovery-required unless the caller is the
  // dedicated recovery path: a broker side effect may already exist.
  bool AllowBrokerSubmittingReclaim = false,
  // Only the broker-reconciliation path may take over an expired
  // `broker_outcome_unknown` record (or an expired `broker_submitting`).
  bool AllowRecoveryAdoption = false
)
{
  // Normal intake never reclaims broker-uncertain states.
  public static readonly CandidateClaimPolicy Default = new();

  public static readonly CandidateClaimPolicy FlipClaim = new(
    AllowRejectedReclaim: true,
    AllowBrokerSubmittingReclaim: false
  );

  public static readonly CandidateClaimPolicy Recovery = new(
    AllowBrokerSubmittingReclaim: true,
    AllowRecoveryAdoption: true
  );
}

public enum CandidateClaimDisposition
{
  Claimed,
  ActiveElsewhere,
  Terminal,
  RecoveryRequired,
  Conflict,
}

public sealed record CandidateClaimResult(
  CandidateClaimDisposition Disposition,
  CandidateExecutionLease? Lease,
  CandidateExecutionRecord? Record
)
{
  public static CandidateClaimResult Conflict(CandidateExecutionRecord? record = null) =>
    new(CandidateClaimDisposition.Conflict, null, record);

  public bool Claimed => Disposition == CandidateClaimDisposition.Claimed;

  // Only a proven-terminal candidate may advance the stream cursor. Anything
  // else is either still owned, retryable, or awaiting broker reconciliation.
  public bool AdvancesCursor => Disposition == CandidateClaimDisposition.Terminal;
}

public sealed record CandidateExecutionRecord(
  string CandidateId,
  string? StreamEventId,
  string State,
  string? LeaseToken,
  long? LeaseExpiresAt,
  string? Outcome,
  long UpdatedAt,
  int Version,
  int Attempt = 0,
  string? LastError = null,
  // True only for plain-string markers parsed during rolling deploy.
  // Never inferred from empty identity on a structured JSON record.
  [property: JsonIgnore]
  bool IsLegacy = false
)
{
  public const int SupportedVersion = 1;

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

  public bool IsTerminal => CandidateExecutionStates.IsTerminal(State);

  public bool HasExactIdentity =>
    !string.IsNullOrWhiteSpace(CandidateId)
    && !string.IsNullOrWhiteSpace(StreamEventId);

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
    if (raw is "published" or "processing")
    {
      return new CandidateExecutionRecord(
        "", null, raw, null, null, null, 0, 1, IsLegacy: true
      );
    }
    if (raw == CandidateExecutionStates.DryRun)
    {
      return new CandidateExecutionRecord(
        "",
        null,
        CandidateExecutionStates.DryRun,
        null,
        null,
        raw,
        0,
        1,
        IsLegacy: true
      );
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
        1,
        IsLegacy: true
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
        1,
        IsLegacy: true
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
        1,
        IsLegacy: true
      );
    }
    ValidateStructuredSchema(raw);
    var record = JsonSerializer.Deserialize(
      raw,
      CandidateExecutionJsonContext.Default.CandidateExecutionRecord
    ) ?? throw new InvalidOperationException("candidate execution record is invalid");
    // Structured JSON is never legacy, even when identity fields are missing.
    return record with { IsLegacy = false };
  }

  // A structured record must explicitly carry a supported integer version, an
  // identity field pair and a known state. Nothing is defaulted: source-
  // generated deserialisation cannot distinguish a missing integer from zero,
  // so the JSON is inspected before parsing. Malformed structured records
  // fail closed identically in Python, C# and Redis Lua.
  private static void ValidateStructuredSchema(string raw)
  {
    using var document = JsonDocument.Parse(raw);
    var root = document.RootElement;
    if (root.ValueKind != JsonValueKind.Object)
    {
      throw new InvalidOperationException(
        "candidate execution record must be a JSON object"
      );
    }
    if (
      !root.TryGetProperty("version", out var version)
      || version.ValueKind != JsonValueKind.Number
      || !version.TryGetInt32(out var parsedVersion)
      || parsedVersion != CandidateExecutionRecord.SupportedVersion
    )
    {
      throw new InvalidOperationException(
        "structured candidate execution record version must explicitly be "
        + CandidateExecutionRecord.SupportedVersion.ToString()
      );
    }
    if (
      !root.TryGetProperty("candidate_id", out var candidateId)
      || candidateId.ValueKind is not (JsonValueKind.String or JsonValueKind.Null)
    )
    {
      throw new InvalidOperationException(
        "structured candidate execution record requires candidate_id"
      );
    }
    if (
      !root.TryGetProperty("stream_event_id", out var streamEventId)
      || streamEventId.ValueKind is not (JsonValueKind.String or JsonValueKind.Null)
    )
    {
      throw new InvalidOperationException(
        "structured candidate execution record requires stream_event_id"
      );
    }
    if (
      !root.TryGetProperty("state", out var state)
      || state.ValueKind != JsonValueKind.String
      || string.IsNullOrWhiteSpace(state.GetString())
      || !CandidateExecutionStates.IsKnown(state.GetString())
    )
    {
      throw new InvalidOperationException(
        "structured candidate execution record requires a known state"
      );
    }
  }

  public static CandidateExecutionRecord? TryParse(string? raw)
  {
    if (string.IsNullOrWhiteSpace(raw))
    {
      return null;
    }
    try
    {
      return Parse(raw);
    }
    catch (InvalidOperationException)
    {
      return null;
    }
    catch (JsonException)
    {
      return null;
    }
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
    catch (JsonException)
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
    if (record.IsLegacy)
    {
      // Legacy plain markers carry no identity; rolling-deploy readers keep
      // the restricted compatibility surface they already had.
      if (!string.IsNullOrWhiteSpace(record.Outcome))
      {
        return record.Outcome.StartsWith("ordered:", StringComparison.Ordinal)
          || record.Outcome.StartsWith("rejected:", StringComparison.Ordinal)
          || record.Outcome.StartsWith("flip_pending:", StringComparison.Ordinal)
          || record.Outcome.Equals(
            CandidateExecutionStates.DryRun,
            StringComparison.Ordinal
          )
          || record.Outcome.StartsWith(
            "already_processed:",
            StringComparison.Ordinal
          );
      }
      return CandidateExecutionStates.IsKnown(record.State)
        || record.State is "published" or "processing";
    }
    // Structured versioned records require exact identity. Missing fields are
    // a conflict, never an implicit pass.
    if (
      record.Version < 1
      || record.Version > CandidateExecutionRecord.SupportedVersion
    )
    {
      return false;
    }
    if (
      string.IsNullOrWhiteSpace(record.CandidateId)
      || string.IsNullOrWhiteSpace(record.StreamEventId)
    )
    {
      return false;
    }
    if (!record.CandidateId.Equals(candidateId, StringComparison.Ordinal))
    {
      return false;
    }
    if (!record.StreamEventId.Equals(streamEventId, StringComparison.Ordinal))
    {
      return false;
    }
    if (!string.IsNullOrWhiteSpace(record.Outcome))
    {
      return record.Outcome.StartsWith("ordered:", StringComparison.Ordinal)
        || record.Outcome.StartsWith("rejected:", StringComparison.Ordinal)
        || record.Outcome.StartsWith("flip_pending:", StringComparison.Ordinal)
        || record.Outcome.Equals(
          CandidateExecutionStates.DryRun,
          StringComparison.Ordinal
        )
        || record.Outcome.StartsWith("already_processed:", StringComparison.Ordinal);
    }
    return CandidateExecutionStates.IsKnown(record.State);
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
    return CandidateExecutionStates.Completed;
  }
}

// Null fields are serialized explicitly so every structured record carries
// the full schema (version, identity, state), exactly like the Lua and
// Python writers. Strict readers require key presence.
[JsonSourceGenerationOptions(
  PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower
)]
[JsonSerializable(typeof(CandidateExecutionRecord))]
internal sealed partial class CandidateExecutionJsonContext : JsonSerializerContext;
