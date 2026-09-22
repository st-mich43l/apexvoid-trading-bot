using Dekaf.Producer;
using Dekaf.Serialization;

namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// Thin wrapper over Dekaf's <c>IProducer&lt;string,byte[]&gt;</c> — the
/// one place this service's actual Kafka client library is visible
/// (ADR-010: <c>Dekaf</c>, not <c>Confluent.Kafka</c> — that ADR's own
/// evidence is why <c>&lt;TrimMode&gt;partial&lt;/TrimMode&gt;</c> is
/// required in CTraderFeed.csproj for this to work under Native AOT).
/// <see cref="KafkaMarketPublisher"/> is the only caller — nothing else
/// in this codebase should construct a Dekaf producer directly (source
/// task §13's transport-area boundary).
/// </summary>
public sealed class KafkaProducer : IAsyncDisposable
{
  private readonly IKafkaProducer<string, byte[]> _producer;

  private KafkaProducer(IKafkaProducer<string, byte[]> producer)
  {
    _producer = producer;
  }

  /// <summary>
  /// Builds a producer against options — idempotent, acks=all (source
  /// task §17/§21: enable idempotence where supported; still assume
  /// application-level duplicates are possible; never lower durability
  /// for benchmark speed). Startup reachability is proven by the first
  /// real produce call's own success/failure, not a separate ping —
  /// Dekaf's <c>BuildAsync</c> itself resolves broker metadata, so a
  /// misconfigured/unreachable broker surfaces here already.
  /// </summary>
  public static async Task<KafkaProducer> CreateAsync(KafkaOptions options, CancellationToken cancellationToken)
  {
    var producer = await Dekaf.Kafka.CreateProducer<string, byte[]>()
      .WithBootstrapServers([.. options.Brokers])
      .WithClientId(options.ClientId)
      .ForReliability()
      .BuildAsync();
    return new KafkaProducer(producer);
  }

  /// <summary>
  /// Publishes one record synchronously with respect to broker
  /// acknowledgement (source task §16: "must not merely enqueue and
  /// return while delivery errors disappear") — awaits
  /// <c>ProduceAsync</c> directly rather than fire-and-forget, and lets
  /// any <see cref="ProduceException"/> propagate to the caller
  /// unchanged (<see cref="KafkaMarketPublisher"/> does not swallow it
  /// either — see that type's own doc comment on why FeedRunner needs to
  /// see this).
  /// </summary>
  public async Task ProduceAsync(string topic, string key, byte[] value, IReadOnlyList<(string Key, string Value)> headers, CancellationToken cancellationToken)
  {
    var builtHeaders = Headers.Create();
    foreach (var (headerKey, headerValue) in headers)
    {
      builtHeaders = builtHeaders.Add(headerKey, headerValue);
    }
    var message = new ProducerMessage<string, byte[]>
    {
      Topic = topic,
      Key = key,
      Value = value,
      Headers = builtHeaders,
    };
    await _producer.ProduceAsync(message, cancellationToken);
  }

  public async Task FlushAsync(CancellationToken cancellationToken) => await _producer.FlushAsync(cancellationToken);

  public async ValueTask DisposeAsync()
  {
    await _producer.DisposeAsync();
  }
}
