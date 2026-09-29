using System.Security.Cryptography;
using System.Text;

namespace ApexVoid.CTraderFeed;

/// <summary>
/// Parses TradePlan broker ownership tokens from order/position comments and
/// ClientOrderIds. Recognizes L1/L2-style leg ids and the legacy 0-based
/// numeric index form used before the P0 ownership fix.
/// Accepts <c>v8|</c> ownership comments only.
/// </summary>
public static class TradePlanOwnership
{
  private const string GoPlanPrefix = "v8:go_opp_";

  public sealed record Ownership(string PlanId, string ThesisId, string LegId);
  public sealed record CompactOwnership(string PlanToken, string LegId);

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

  public static bool IsTradePlanOwnershipComment(string? comment) =>
    !string.IsNullOrWhiteSpace(comment)
    && (
      comment.StartsWith("v8|", StringComparison.Ordinal)
      || comment.StartsWith("v8c|", StringComparison.Ordinal)
    );

  private static Ownership? TryParseComment(string? comment)
  {
    if (string.IsNullOrWhiteSpace(comment))
    {
      return null;
    }
    var parts = comment.Split('|');
    if (
      parts.Length == 3
      && parts[0] == "v8c"
      && TryNormalizeLegId(parts[2]) is { } compactLeg
      && TryDecodeGoPlanToken(parts[1]) is { } compactPlanId
    )
    {
      return new Ownership(compactPlanId, "", compactLeg);
    }
    if (parts.Length < 3 || parts[0] != "v8")
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

  public static CompactOwnership? TryParseCompactComment(string? comment)
  {
    if (string.IsNullOrWhiteSpace(comment))
    {
      return null;
    }
    var parts = comment.Split('|');
    return parts.Length == 3
      && parts[0] == "v8c"
      && !string.IsNullOrWhiteSpace(parts[1])
      && TryNormalizeLegId(parts[2]) is { } legId
        ? new CompactOwnership(parts[1], legId)
        : null;
  }

  private static Ownership? TryParseClientOrderId(string? clientOrderId)
  {
    if (string.IsNullOrWhiteSpace(clientOrderId))
    {
      return null;
    }
    var separator = clientOrderId.LastIndexOf(':');
    if (separator > 0 && separator < clientOrderId.Length - 1)
    {
      var planId = clientOrderId[..separator];
      var legToken = clientOrderId[(separator + 1)..];
      if (!string.IsNullOrWhiteSpace(planId) && TryNormalizeLegId(legToken) is { } legId)
      {
        return new Ownership(planId, "", legId);
      }
    }

    // Long Go opportunity IDs are encoded reversibly as a compact base64url
    // payload so the broker's 50-character ClientOrderId limit does not lose
    // the exact plan identity during restart/reconciliation.
    if (clientOrderId.StartsWith('g'))
    {
      var compactSeparator = clientOrderId.LastIndexOf('.');
      if (
        compactSeparator > 1
        && compactSeparator < clientOrderId.Length - 1
        && TryNormalizeLegId(clientOrderId[(compactSeparator + 1)..]) is { } compactLeg
      )
      {
        var planId = TryDecodeGoPlanToken(clientOrderId[1..compactSeparator]);
        return planId is null ? null : new Ownership(planId, "", compactLeg);
      }
    }
    return null;
  }

  /// <summary>
  /// Maps "L1"/"l1" and legacy 0-based indices ("0"→L1, "1"→L2) to canonical
  /// L{n} ids, and the reserved "RISK" reaction-leg id to itself. Returns
  /// null when the token is not a recognisable leg id.
  /// </summary>
  public static string? TryNormalizeLegId(string? token)
  {
    if (string.IsNullOrWhiteSpace(token))
    {
      return null;
    }
    // ENTRY_LOGIC_REVIEW_2026-09-17: without this, a filled RISK leg's
    // position can never be adopted here (comment/ClientOrderId both carry
    // the literal "RISK" token) - the leg stays "Pending" forever and the
    // orphan-pending-order path below eventually mislabels a real, broker-
    // protected fill as "Cancelled".
    if (string.Equals(token, "RISK", StringComparison.OrdinalIgnoreCase))
    {
      return "RISK";
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

  // cTrader bounds broker metadata. Go plan IDs carry a full opportunity
  // hash and can exceed that limit when combined with the thesis ID. The
  // exact identity remains in the persisted runtime leg and deterministic
  // ClientOrderId; the comment is only a compact diagnostic token. Its v8c
  // prefix prevents the legacy full ownership parser from treating a
  // truncated token as an exact plan ID.
  public static string FormatComment(string planId, string thesisId, string legId) =>
    $"v8c|{TryEncodeGoPlanId(planId) ?? LegacyPlanToken(planId)}|{legId}";

  public static string FormatClientOrderId(string planId, string legId) =>
    planId.Length + legId.Length + 1 <= 50
      ? $"{planId}:{legId}"
      : TryEncodeGoPlanId(planId) is { } encoded
        ? $"g{encoded}.{legId}"
        : $"h{LegacyPlanToken(planId)}.{legId}";

  public static IReadOnlyList<string> PlanTokens(string planId)
  {
    var tokens = new List<string> { LegacyPlanToken(planId) };
    if (TryEncodeGoPlanId(planId) is { } encoded)
    {
      tokens.Add(encoded);
    }
    return tokens;
  }

  private static string? TryEncodeGoPlanId(string planId)
  {
    if (!planId.StartsWith(GoPlanPrefix, StringComparison.Ordinal))
    {
      return null;
    }
    var suffix = planId[GoPlanPrefix.Length..];
    if (suffix.Length != 64 || suffix.Any(value => !Uri.IsHexDigit(value)))
    {
      return null;
    }
    return Convert.ToBase64String(Convert.FromHexString(suffix))
      .TrimEnd('=').Replace('+', '-').Replace('/', '_');
  }

  private static string? TryDecodeGoPlanToken(string token)
  {
    try
    {
      var encoded = token.Replace('-', '+').Replace('_', '/');
      encoded = encoded.PadRight(encoded.Length + (4 - encoded.Length % 4) % 4, '=');
      var bytes = Convert.FromBase64String(encoded);
      return bytes.Length == 32
        ? GoPlanPrefix + Convert.ToHexString(bytes).ToLowerInvariant()
        : null;
    }
    catch (FormatException)
    {
      return null;
    }
  }

  private static string LegacyPlanToken(string planId) =>
    Convert.ToHexString(
      SHA256.HashData(Encoding.UTF8.GetBytes(planId))
    )[..24].ToLowerInvariant();
}
