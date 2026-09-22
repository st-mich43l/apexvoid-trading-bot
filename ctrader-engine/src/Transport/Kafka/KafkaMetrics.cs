namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// Bounded-cardinality Kafka producer metrics (source task §46):
/// <c>market_bar_publish_total</c>, <c>market_bar_publish_error_total</c>,
/// <c>market_bar_publish_duration_ms</c>,
/// <c>market_bar_recovery_publish_total</c>. Hand-rolled, no metrics
/// library dependency — mirrors analysis-engine's own
/// <c>internal/transport/kafka.Metrics</c> (sync.Mutex+map there, a
/// plain lock+dictionary here); this project has no existing metrics
/// library to reuse either. Labels are bounded (symbol, timeframe) —
/// never a raw event ID, price, or offset (source task §46's "keep
/// bounded dimensions").
/// </summary>
public sealed class KafkaMetrics
{
  private readonly object _lock = new();
  private readonly Dictionary<(string Symbol, string Timeframe), long> _publishTotal = [];
  private readonly Dictionary<(string Symbol, string Timeframe), long> _publishErrorTotal = [];
  private readonly Dictionary<(string Symbol, string Timeframe), long> _recoveryPublishTotal = [];
  private readonly Dictionary<(string Symbol, string Timeframe), (double TotalMs, long Samples)> _publishDuration = [];

  public void RecordPublish(string symbol, string timeframe, TimeSpan duration, bool isRecovery)
  {
    lock (_lock)
    {
      var key = (symbol, timeframe);
      _publishTotal[key] = _publishTotal.GetValueOrDefault(key) + 1;
      if (isRecovery)
      {
        _recoveryPublishTotal[key] = _recoveryPublishTotal.GetValueOrDefault(key) + 1;
      }
      var (totalMs, samples) = _publishDuration.GetValueOrDefault(key);
      _publishDuration[key] = (totalMs + duration.TotalMilliseconds, samples + 1);
    }
  }

  public void RecordPublishError(string symbol, string timeframe)
  {
    lock (_lock)
    {
      var key = (symbol, timeframe);
      _publishErrorTotal[key] = _publishErrorTotal.GetValueOrDefault(key) + 1;
    }
  }

  public KafkaMetricsSnapshot Snapshot()
  {
    lock (_lock)
    {
      return new KafkaMetricsSnapshot(
        new Dictionary<(string, string), long>(_publishTotal),
        new Dictionary<(string, string), long>(_publishErrorTotal),
        new Dictionary<(string, string), long>(_recoveryPublishTotal),
        new Dictionary<(string, string), (double, long)>(_publishDuration)
      );
    }
  }
}

public sealed record KafkaMetricsSnapshot(
  IReadOnlyDictionary<(string Symbol, string Timeframe), long> PublishTotal,
  IReadOnlyDictionary<(string Symbol, string Timeframe), long> PublishErrorTotal,
  IReadOnlyDictionary<(string Symbol, string Timeframe), long> RecoveryPublishTotal,
  IReadOnlyDictionary<(string Symbol, string Timeframe), (double TotalMs, long Samples)> PublishDuration
);
