namespace ApexVoid.CTraderFeed;

public sealed class LiveBarQualityMonitor(int lookback, Action<string> warning)
{
  private sealed record State(long Timestamp, string? Extreme, int Count);

  private readonly int _lookback = Math.Max(1, lookback);
  private readonly Dictionary<string, State> _states = new(
    StringComparer.OrdinalIgnoreCase
  );

  public void Observe(string timeframe, OhlcBar bar)
  {
    var key = timeframe.ToUpperInvariant();
    if (_states.TryGetValue(key, out var prior) && bar.Timestamp <= prior.Timestamp)
    {
      return;
    }

    var extreme = Extreme(bar);
    var count = extreme is not null && prior?.Extreme == extreme
      ? prior.Count + 1
      : extreme is null ? 0 : 1;
    _states[key] = new State(bar.Timestamp, extreme, count);
    if (extreme is not null && count >= _lookback)
    {
      warning(
        $"live bars closing at range extreme {count} in a row - close-source suspect "
        + $"tf={key} side={extreme} ts={bar.Timestamp}"
      );
    }
  }

  private static string? Extreme(OhlcBar bar)
  {
    if (bar.High <= bar.Low)
    {
      return null;
    }
    if (bar.Close == bar.Low)
    {
      return "low";
    }
    return bar.Close == bar.High ? "high" : null;
  }
}
