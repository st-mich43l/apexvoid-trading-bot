namespace ApexVoid.CTraderFeed;

public sealed class ClosedBarEmitter
{
  private readonly Dictionary<string, OhlcBar> _forming = new(
    StringComparer.OrdinalIgnoreCase
  );
  private readonly HashSet<string> _emitted = new(StringComparer.OrdinalIgnoreCase);

  public IReadOnlyList<OhlcBar> Observe(string timeframe, OhlcBar liveBar)
  {
    var key = timeframe.ToUpperInvariant();
    if (!_forming.TryGetValue(key, out var current))
    {
      _forming[key] = liveBar;
      return Array.Empty<OhlcBar>();
    }

    if (liveBar.Timestamp == current.Timestamp)
    {
      _forming[key] = liveBar;
      return Array.Empty<OhlcBar>();
    }

    if (liveBar.Timestamp < current.Timestamp)
    {
      return Array.Empty<OhlcBar>();
    }

    _forming[key] = liveBar;
    var emittedKey = $"{key}:{current.Timestamp}";
    return _emitted.Add(emittedKey)
      ? new[] { current }
      : Array.Empty<OhlcBar>();
  }
}
