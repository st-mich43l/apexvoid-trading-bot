namespace ApexVoid.CTraderFeed;

public sealed class SpotHistory(long retentionSeconds = 7_200)
{
  private readonly object _lock = new();
  private readonly Dictionary<string, SortedDictionary<long, decimal>> _bids = new(
    StringComparer.OrdinalIgnoreCase
  );
  private readonly long _retentionSeconds = Math.Max(1, retentionSeconds);

  public void Observe(SpotPrice spot)
  {
    lock (_lock)
    {
      if (!_bids.TryGetValue(spot.Symbol, out var series))
      {
        series = [];
        _bids[spot.Symbol] = series;
      }
      series[spot.Timestamp] = spot.Bid;
      var cutoff = spot.Timestamp - _retentionSeconds;
      foreach (var timestamp in series.Keys.TakeWhile(item => item < cutoff).ToArray())
      {
        series.Remove(timestamp);
      }
    }
  }

  public bool TryLastBid(
    string symbol,
    long periodOpen,
    long periodClose,
    out decimal bid
  )
  {
    lock (_lock)
    {
      if (_bids.TryGetValue(symbol, out var series))
      {
        foreach (var item in series.Reverse())
        {
          if (item.Key >= periodClose)
          {
            continue;
          }
          if (item.Key < periodOpen)
          {
            break;
          }
          bid = item.Value;
          return true;
        }
      }
    }
    bid = 0;
    return false;
  }
}
