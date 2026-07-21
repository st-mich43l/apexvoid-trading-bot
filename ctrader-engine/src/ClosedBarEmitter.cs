namespace ApexVoid.CTraderFeed;

public sealed class ClosedBarEmitter(SpotHistory spots, string symbol)
{
  private readonly Dictionary<string, OhlcBar> _forming = new(
    StringComparer.OrdinalIgnoreCase
  );
  private readonly HashSet<string> _emitted = new(StringComparer.OrdinalIgnoreCase);

  public IReadOnlyList<ClosedBarEmission> Observe(string timeframe, OhlcBar liveBar)
  {
    var key = timeframe.ToUpperInvariant();
    if (!_forming.TryGetValue(key, out var current))
    {
      _forming[key] = liveBar;
      return Array.Empty<ClosedBarEmission>();
    }

    if (liveBar.Timestamp == current.Timestamp)
    {
      _forming[key] = liveBar;
      return Array.Empty<ClosedBarEmission>();
    }

    if (liveBar.Timestamp < current.Timestamp)
    {
      return Array.Empty<ClosedBarEmission>();
    }

    _forming[key] = liveBar;
    var emittedKey = $"{key}:{current.Timestamp}";
    if (!_emitted.Add(emittedKey))
    {
      return Array.Empty<ClosedBarEmission>();
    }

    var periodClose = current.CloseTimestamp(timeframe);
    if (!spots.TryLastBid(symbol, current.Timestamp, periodClose, out var bid))
    {
      return [new ClosedBarEmission(current, RequiresHistoricalClose: true)];
    }
    var close = Math.Clamp(bid, current.Low, current.High);
    return [new ClosedBarEmission(
      current with { Close = close },
      RequiresHistoricalClose: false
    )];
  }
}
