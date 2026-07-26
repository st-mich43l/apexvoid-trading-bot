using System.Text.Json;

namespace ApexVoid.CTraderFeed;

public sealed record LifecycleTransition(
  bool MutatesCurrentState,
  string? State,
  bool Terminal
);

public enum LifecycleTransitionOutcome
{
  Applied,
  Duplicate,
  Invalid,
  Recovery,
}

public sealed record AutoTradeLifecycleStateRecord(
  string OwnerId,
  string State,
  string? PreviousState,
  string EventType,
  string EventId,
  string? CandidateId,
  string? GroupId,
  long? PositionId,
  long UpdatedAt,
  bool Terminal,
  int Version = 1
);

public static class AutoTradeLifecycle
{
  public const int StateRecordVersion = 1;

  private static readonly Dictionary<string, int> StateRank =
    new(StringComparer.OrdinalIgnoreCase)
    {
      ["executor_received"] = 10,
      ["routing_selected"] = 20,
      ["order_planned"] = 30,
      ["waiting_for_price"] = 35,
      ["order_submitted"] = 40,
      ["order_accepted"] = 50,
      ["order_filled"] = 60,
      ["managing"] = 70,
      ["partially_closed"] = 80,
      // Recovery states rank below `executor_received` so a retry advancing to
      // any real execution state is never treated as a backward transition.
      ["retryable_error"] = 5,
      ["broker_outcome_unknown"] = 45,
      ["closed"] = 100,
      ["rejected"] = 100,
      ["expired"] = 100,
      ["cancelled"] = 100,
      ["invalidated"] = 100,
      ["error"] = 100,
      ["integrity_error"] = 100,
    };

  // Nonterminal states a candidate can occupy while it waits for a retry or a
  // broker reconciliation. Later execution states must remain reachable.
  private static readonly HashSet<string> NonTerminalRecoveryStates =
    new(StringComparer.OrdinalIgnoreCase)
    {
      "retryable_error",
      "broker_outcome_unknown",
    };

  public static LifecycleTransition? TransitionForEvent(
    string type,
    long? remainingVolume
  ) => type switch
  {
    "executor_received" => new(true, "executor_received", false),
    "routing_selected" => new(true, "routing_selected", false),
    "order_planned" => new(true, "order_planned", false),
    "zone_planned" or "manual_planned" => new(true, "waiting_for_price", false),
    "order_submitted" => new(true, "order_submitted", false),
    "order_accepted" => new(true, "order_accepted", false),
    "opened" or "manual_opened" or "add" => new(true, "order_filled", false),
    "managing" => new(true, "managing", false),
    "take_profit" when remainingVolume is > 0 => new(true, "partially_closed", false),
    "take_profit" => new(true, "closed", true),
    "position_closed" or "manual_closed" or "group_result" => new(true, "closed", true),
    "rejected" => new(true, "rejected", true),
    "zone_expired" or "manual_expired" => new(true, "expired", true),
    "manual_cancelled" or "cancelled" => new(true, "cancelled", true),
    "invalidated" => new(true, "invalidated", true),
    // A retryable operational error is not a failed trade: the candidate can
    // still be reclaimed and reach order_filled.
    "candidate_retryable_error" => new(true, "retryable_error", false),
    "candidate_lease_lost" => new(false, null, false),
    // The broker may hold an order. Recovery-required, never terminal.
    "broker_outcome_unknown" => new(true, "broker_outcome_unknown", false),
    // Identity invariants were violated: block further execution.
    "candidate_integrity_error" => new(true, "integrity_error", true),
    "candidate_terminal_error" => new(true, "error", true),
    "manual_command_error" or "config_fatal" => new(true, "error", true),
    // `service_error` and any unmapped type are telemetry only: they append
    // history and never create or mutate candidate lifecycle state.
    _ => null,
  };

  public static string? ParseState(string? raw)
  {
    if (string.IsNullOrWhiteSpace(raw))
    {
      return null;
    }
    if (raw.TrimStart().StartsWith("{", StringComparison.Ordinal))
    {
      try
      {
        using var document = JsonDocument.Parse(raw);
        if (
          document.RootElement.TryGetProperty("state", out var state)
          && state.ValueKind == JsonValueKind.String
        )
        {
          return state.GetString();
        }
      }
      catch (JsonException)
      {
      }
    }
    return raw;
  }

  public static (
    LifecycleTransitionOutcome Outcome,
    string? AppliedState
  ) EvaluateTransition(
    string? currentState,
    LifecycleTransition transition,
    string eventType
  )
  {
    var targetState = transition.State
      ?? throw new InvalidOperationException(
        $"lifecycle transition for {eventType} has no target state"
      );
    if (string.IsNullOrWhiteSpace(currentState))
    {
      return (LifecycleTransitionOutcome.Applied, targetState);
    }
    if (string.Equals(currentState, targetState, StringComparison.OrdinalIgnoreCase))
    {
      return (LifecycleTransitionOutcome.Duplicate, targetState);
    }
    if (IsRecoveryTransition(currentState, targetState))
    {
      return (LifecycleTransitionOutcome.Recovery, targetState);
    }
    if (IsInvalidTransition(currentState, targetState))
    {
      return (LifecycleTransitionOutcome.Invalid, null);
    }
    return (LifecycleTransitionOutcome.Applied, targetState);
  }

  public static string? RangeSideStateFor(string lifecycleState) =>
    lifecycleState switch
    {
      "order_submitted" or "order_accepted" => "ORDER_SUBMITTED",
      "order_filled" => "ORDER_FILLED",
      "managing" or "partially_closed" => "MANAGING",
      "closed" => "CLOSED",
      "rejected" or "expired" or "cancelled" => "REARMED",
      _ => null,
    };

  public static AutoTradeLifecycleStateRecord BuildStateRecord(
    AutoTradeEvent tradeEvent,
    string ownerId,
    bool terminal
  ) => new(
    ownerId,
    tradeEvent.State!,
    tradeEvent.PreviousState,
    tradeEvent.Type,
    tradeEvent.LifecycleId ?? string.Empty,
    tradeEvent.CandidateId,
    tradeEvent.GroupId,
    tradeEvent.PositionId,
    tradeEvent.Timestamp,
    terminal,
    StateRecordVersion
  );

  public static bool IsNonTerminalRecoveryState(string? state) =>
    state is not null && NonTerminalRecoveryStates.Contains(state);

  private static bool IsTerminal(string state) =>
    !NonTerminalRecoveryStates.Contains(state)
    && (
      state.Equals("closed", StringComparison.OrdinalIgnoreCase)
      || state.Equals("rejected", StringComparison.OrdinalIgnoreCase)
      || state.Equals("expired", StringComparison.OrdinalIgnoreCase)
      || state.Equals("cancelled", StringComparison.OrdinalIgnoreCase)
      || state.Equals("invalidated", StringComparison.OrdinalIgnoreCase)
      || state.Equals("error", StringComparison.OrdinalIgnoreCase)
      || state.Equals("integrity_error", StringComparison.OrdinalIgnoreCase)
    );

  private static int Rank(string state) =>
    StateRank.TryGetValue(state, out var rank) ? rank : 0;

  private static bool IsRecoveryTransition(string current, string target) =>
    (current, target) switch
    {
      ("executor_received", "order_filled") => true,
      ("order_submitted", "order_filled") => true,
      ("order_accepted", "order_filled") => true,
      // A candidate parked in a nonterminal recovery state is still live: a
      // reclaimed retry re-enters the execution sequence from the start, and
      // broker adoption jumps straight to accepted/filled. Neither is a
      // backward transition, so rank must not veto them.
      (var currentState, _) when IsNonTerminalRecoveryState(currentState) => true,
      // A live candidate may always fall back into a recovery state; only a
      // terminal lifecycle refuses to reopen.
      (var currentState, var targetState)
        when IsNonTerminalRecoveryState(targetState)
          && !IsTerminal(currentState) => true,
      (var currentState, "managing")
        when !IsTerminal(currentState)
          && Rank(currentState) < Rank("managing") => true,
      _ => false,
    };

  private static bool IsInvalidTransition(string current, string target)
  {
    if (
      current.Equals("closed", StringComparison.OrdinalIgnoreCase)
      && target.Equals("managing", StringComparison.OrdinalIgnoreCase)
    )
    {
      return true;
    }
    if (
      current.Equals("rejected", StringComparison.OrdinalIgnoreCase)
      && target.Equals("order_submitted", StringComparison.OrdinalIgnoreCase)
    )
    {
      return true;
    }
    if (
      current.Equals("order_filled", StringComparison.OrdinalIgnoreCase)
      && target.Equals("routing_selected", StringComparison.OrdinalIgnoreCase)
    )
    {
      return true;
    }
    if (IsTerminal(current) && !IsTerminal(target))
    {
      return true;
    }
    return Rank(target) < Rank(current)
      && !IsRecoveryTransition(current, target);
  }
}
