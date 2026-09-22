namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// Kafka transport liveness/readiness state (source task §43) — the
/// .NET mirror of analysis-engine's <c>kafka.Health</c>/<c>kafka.Snapshot</c>.
/// Distinguishes "the process is alive" from "Kafka is actually usable":
/// when <c>KAFKA_REQUIRED</c>-equivalent behavior matters (source task
/// §43: "if Kafka is required for market events, cTrader must not
/// advertise full readiness when Kafka publication is unavailable"),
/// <see cref="HealthFile"/>/the process healthcheck reads
/// <see cref="Ready"/>, not just process-alive.
/// </summary>
public sealed class KafkaHealth(bool configured)
{
  // A plain object monitor, not System.Threading.Lock — that type is
  // .NET 9+ only, and this project targets net8.0 (matching
  // cTrader.OpenAPI.Net / StackExchange.Redis's own target).
  private readonly object _lock = new();
  private bool _connected;
  private bool _producerReady;
  private DateTimeOffset? _lastProduceAt;
  private Exception? _lastError;
  private DateTimeOffset? _lastErrorAt;

  public void SetConnected(bool value)
  {
    lock (_lock) { _connected = value; }
  }

  public void SetProducerReady(bool value)
  {
    lock (_lock) { _producerReady = value; }
  }

  public void MarkProduced(DateTimeOffset at)
  {
    lock (_lock) { _lastProduceAt = at; }
  }

  public void MarkError(Exception error, DateTimeOffset at)
  {
    lock (_lock) { _lastError = error; _lastErrorAt = at; }
  }

  public KafkaHealthSnapshot Snapshot()
  {
    lock (_lock)
    {
      return new KafkaHealthSnapshot(configured, _connected, _producerReady, _lastProduceAt, _lastError, _lastErrorAt);
    }
  }
}

public sealed record KafkaHealthSnapshot(
  bool Configured,
  bool Connected,
  bool ProducerReady,
  DateTimeOffset? LastProduceAt,
  Exception? LastError,
  DateTimeOffset? LastErrorAt
)
{
  /// <summary>
  /// Readiness-vs-liveness split (source task §43/§68's own Go analogue):
  /// when Kafka isn't configured, readiness never depends on it. When it
  /// is, ready only once actually connected AND the producer is up —
  /// never "ready" merely because the process is alive.
  /// </summary>
  public bool Ready => !Configured || (Connected && ProducerReady);
}
