namespace ApexVoid.CTraderFeed;

public sealed class AutoTradeConfigurationException(string message)
  : InvalidOperationException(message)
{
  public static AutoTradeConfigurationException AccountNotGranted(
    long accountId,
    IReadOnlyList<TradingAccountGrant> grants
  )
  {
    var granted = grants.Count == 0
      ? "none"
      : string.Join(
        ", ",
        grants.Select(item => $"{item.AccountId} {(item.IsLive ? "live" : "demo")}")
      );
    return new AutoTradeConfigurationException(
      $"Auto trade disabled: account {accountId} is not granted to the current "
      + $"access token (granted: {granted}). Re-authorize the app for {accountId}, "
      + "put the new tokens in .env, then restart — the cached rotation chain "
      + "resets automatically when the .env token changes."
    );
  }
}
