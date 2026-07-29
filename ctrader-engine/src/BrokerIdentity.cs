namespace ApexVoid.CTraderFeed;

public static class BrokerIdentity
{
  public static string Canonical(string value)
  {
    var compact = string.Concat(
      value.Trim().ToLowerInvariant().Where(char.IsLetterOrDigit)
    );
    return compact switch
    {
      "fpmarkets" or "fpmarketssc" => "fpmarkets",
      "fusion" or "fusionmarkets" => "fusionmarkets",
      "icmarkets" or "icmarketsau" or "icmarketssc" => "icmarkets",
      _ => compact,
    };
  }

  public static bool Matches(string actual, string expected) =>
    string.IsNullOrWhiteSpace(expected)
    || Canonical(actual) == Canonical(expected);
}
