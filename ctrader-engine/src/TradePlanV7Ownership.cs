namespace ApexVoid.CTraderFeed;

/// <summary>
/// Parses TradePlan broker ownership tokens from order/position comments and
/// ClientOrderIds. Recognizes L1/L2-style leg ids and the legacy 0-based
/// numeric index form used before the P0 ownership fix.
/// Accepts both v7| and v8| prefixes during the V7→V8 drain window; new
/// comments are always formatted as v8|.
/// </summary>
public static class TradePlanV7Ownership
{
  public sealed record Ownership(string PlanId, string ThesisId, string LegId);

  public static Ownership? TryParseV7Ownership(
    string? comment,
    string? clientOrderId
  ) => TryParseOwnership(comment, clientOrderId);

  public static Ownership? TryParseOwnership(
    string? comment,
    string? clientOrderId
  )
  {
    if (TryParseComment(comment) is { } fromComment)
    {
      return fromComment;
    }
    return TryParseClientOrderId(clientOrderId);
  }

  public static bool IsV7OwnershipComment(string? comment) =>
    IsTradePlanOwnershipComment(comment);

  public static bool IsTradePlanOwnershipComment(string? comment) =>
    !string.IsNullOrWhiteSpace(comment)
    && (
      comment.StartsWith("v8|", StringComparison.Ordinal)
      || comment.StartsWith("v7|", StringComparison.Ordinal)
    );

  private static Ownership? TryParseComment(string? comment)
  {
    if (string.IsNullOrWhiteSpace(comment))
    {
      return null;
    }
    var parts = comment.Split('|');
    if (
      parts.Length < 3
      || (parts[0] != "v8" && parts[0] != "v7")
    )
    {
      return null;
    }
    var planId = parts[1];
    var thesisId = parts[2];
    if (string.IsNullOrWhiteSpace(planId) || string.IsNullOrWhiteSpace(thesisId))
    {
      return null;
    }
    // market_watch historically omitted the leg token; treat as L1.
    if (parts.Length == 3)
    {
      return new Ownership(planId, thesisId, "L1");
    }
    if (parts.Length >= 4 && TryNormalizeLegId(parts[3]) is { } legId)
    {
      return new Ownership(planId, thesisId, legId);
    }
    return null;
  }

  private static Ownership? TryParseClientOrderId(string? clientOrderId)
  {
    if (string.IsNullOrWhiteSpace(clientOrderId))
    {
      return null;
    }
    var separator = clientOrderId.LastIndexOf(':');
    if (separator <= 0 || separator >= clientOrderId.Length - 1)
    {
      return null;
    }
    var planId = clientOrderId[..separator];
    var legToken = clientOrderId[(separator + 1)..];
    if (string.IsNullOrWhiteSpace(planId) || TryNormalizeLegId(legToken) is not { } legId)
    {
      return null;
    }
    // ClientOrderId does not carry thesis_id; callers that only have this
    // form still get a usable planId+legId with an empty thesis placeholder
    // so reconcile can match against persisted Legs[].ClientOrderId.
    return new Ownership(planId, "", legId);
  }

  /// <summary>
  /// Maps "L1"/"l1" and legacy 0-based indices ("0"→L1, "1"→L2) to canonical
  /// L{n} ids. Returns null when the token is not a recognisable leg id.
  /// </summary>
  public static string? TryNormalizeLegId(string? token)
  {
    if (string.IsNullOrWhiteSpace(token))
    {
      return null;
    }
    if (
      token.Length >= 2
      && (token[0] == 'L' || token[0] == 'l')
      && int.TryParse(token[1..], out var numbered)
      && numbered >= 1
    )
    {
      return $"L{numbered}";
    }
    if (int.TryParse(token, out var zeroBased) && zeroBased >= 0)
    {
      return $"L{zeroBased + 1}";
    }
    return null;
  }

  public static string FormatComment(string planId, string thesisId, string legId) =>
    $"v8|{planId}|{thesisId}|{legId}";

  public static string FormatClientOrderId(string planId, string legId) =>
    $"{planId}:{legId}";
}
