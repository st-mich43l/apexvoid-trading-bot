namespace ApexVoid.CTraderFeed;

/// <summary>
/// Resolves the equity figure used for V7 equity-table sizing at arm/submit.
/// OpenAPI.Net 1.4.4's <c>ProtoOATrader</c> has no Equity field, so the live
/// feed client currently copies Balance into Equity and marks
/// <see cref="TradingAccountSnapshot.EquitySource"/> as
/// <c>balance_proxy</c>. Tests (and any future broker equity source) set
/// Equity independently with a non-proxy source.
/// </summary>
public sealed record EquityResolution(
  decimal Equity,
  string EquitySource,
  decimal AccountBalance,
  decimal AccountEquity,
  long EquitySnapshotTs,
  int OpenPositionCount,
  int PendingOrderCount
);

public static class EquityResolver
{
  public const string SourceAccountEquity = "account_equity";
  public const string SourceBalanceFlatFallback = "balance_flat_account_fallback";
  public const string SourceBalancePlusUnrealized = "balance_plus_unrealized";
  public const string SourceBalanceProxy = "balance_proxy";

  public const string UnavailableWithOpenExposure =
    "equity_unavailable_with_open_exposure";

  /// <summary>
  /// Prefer a real account equity when present; otherwise fall back to
  /// Balance only on a flat account, or Balance + sum(position NetProfit)
  /// when unrealized P/L is available on positions.
  /// </summary>
  public static EquityResolution Resolve(
    TradingAccountSnapshot account,
    int openPositionCount,
    int pendingOrderCount,
    IReadOnlyList<TradingPosition>? positions = null
  )
  {
    var balance = account.Balance;
    var rawEquity = account.Equity;
    var snapshotTs = account.SnapshotTimestamp;
    var hasExposure = openPositionCount > 0 || pendingOrderCount > 0;

    if (HasRealEquity(account))
    {
      return new EquityResolution(
        Equity: rawEquity,
        EquitySource: SourceAccountEquity,
        AccountBalance: balance,
        AccountEquity: rawEquity,
        EquitySnapshotTs: snapshotTs,
        OpenPositionCount: openPositionCount,
        PendingOrderCount: pendingOrderCount
      );
    }

    var unrealized = SumUnrealized(positions);
    if (unrealized is decimal sum && balance > 0)
    {
      var computed = balance + sum;
      if (computed > 0)
      {
        return new EquityResolution(
          Equity: computed,
          EquitySource: SourceBalancePlusUnrealized,
          AccountBalance: balance,
          AccountEquity: rawEquity,
          EquitySnapshotTs: snapshotTs,
          OpenPositionCount: openPositionCount,
          PendingOrderCount: pendingOrderCount
        );
      }
    }

    if (!hasExposure && balance > 0)
    {
      return new EquityResolution(
        Equity: balance,
        EquitySource: SourceBalanceFlatFallback,
        AccountBalance: balance,
        AccountEquity: rawEquity,
        EquitySnapshotTs: snapshotTs,
        OpenPositionCount: openPositionCount,
        PendingOrderCount: pendingOrderCount
      );
    }

    if (hasExposure)
    {
      throw new TradePlanContractException(UnavailableWithOpenExposure);
    }

    throw new TradePlanContractException(
      "account equity/balance must be positive"
    );
  }

  public static string FormatTelemetry(EquityResolution resolved) =>
    $"account_balance={resolved.AccountBalance:0.####} "
    + $"account_equity={resolved.AccountEquity:0.####} "
    + $"equity_source={resolved.EquitySource} "
    + $"equity_snapshot_ts={resolved.EquitySnapshotTs} "
    + $"open_position_count={resolved.OpenPositionCount} "
    + $"pending_order_count={resolved.PendingOrderCount} "
    + $"resolved_equity={resolved.Equity:0.####}";

  private static bool HasRealEquity(TradingAccountSnapshot account)
  {
    if (account.Equity <= 0)
    {
      return false;
    }
    var source = account.EquitySource?.Trim() ?? "";
    // Live ProtoOATrader path marks Balance copied into Equity as proxy.
    // Empty / unknown sources with Equity > 0 are treated as real so tests
    // and future broker-backed snapshots keep working without a flag.
    return source is not (
      SourceBalanceProxy
      or "balance_as_equity_proxy"
    );
  }

  private static decimal? SumUnrealized(IReadOnlyList<TradingPosition>? positions)
  {
    if (positions is null || positions.Count == 0)
    {
      return null;
    }
    decimal? total = null;
    foreach (var position in positions)
    {
      if (position.NetProfit is not decimal profit)
      {
        continue;
      }
      total = (total ?? 0m) + profit;
    }
    return total;
  }
}
