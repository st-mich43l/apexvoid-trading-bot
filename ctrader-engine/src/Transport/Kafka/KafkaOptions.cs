namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// ctrader-engine's Kafka transport configuration — read directly from
/// Configuration V3 (<see cref="ConfigurationV3"/>/<see cref="ConfigDocument"/>),
/// never from environment variables and never added to
/// <see cref="ResolvedRuntimeManifest"/> as a second authority (source
/// task §14/§15). This is the one place Kafka transport settings are
/// read from config — everything downstream (<see cref="KafkaProducer"/>,
/// <see cref="KafkaMarketPublisher"/>) takes a plain <see cref="KafkaOptions"/>
/// value, mirroring analysis-engine's own
/// <c>internal/engine/config.go</c>: "engine is the one place
/// config.Document is read" discipline, applied here to Program.cs's
/// composition root instead.
/// </summary>
public sealed record KafkaOptions(
  bool Enabled,
  IReadOnlyList<string> Brokers,
  string ClientId,
  string MarketBarClosedTopic
)
{
  /// <summary>
  /// Reads transport.kafka.* from doc. Fails closed (throws
  /// <see cref="ConfigurationV3Error"/>) for any missing/malformed
  /// required field — mirroring
  /// <c>internal/transport/kafka.Config.Validate</c>'s fail-closed
  /// discipline on the Go side, source task §5.
  /// </summary>
  public static KafkaOptions FromConfig(ConfigDocument doc)
  {
    var enabled = RequireBool(doc, "transport.kafka.enabled");
    var brokersRaw = doc.Get("transport.kafka.brokers");
    if (brokersRaw is not List<object?> brokersList || brokersList.Count == 0)
    {
      throw new ConfigurationV3Error("transport.kafka.brokers must be a non-empty list");
    }
    var brokers = brokersList
      .Select((item, index) => item as string
        ?? throw new ConfigurationV3Error($"transport.kafka.brokers[{index}] is not a string"))
      .ToArray();

    var clientId = RequireString(doc, "transport.kafka.client_id.ctrader_engine");
    var marketBarClosedTopic = RequireString(doc, "transport.kafka.topics.market_bar_closed");

    var options = new KafkaOptions(enabled, brokers, clientId, marketBarClosedTopic);
    options.Validate();
    return options;
  }

  /// <summary>
  /// Fail-closed validation — mirrors <c>kafka.Config.Validate</c>'s
  /// exact rules on the Go side (source task §5): no brokers, an empty
  /// broker address, a missing topic, or an invalid client ID must all
  /// be rejected before any producer is constructed. When Enabled is
  /// false, nothing below is checked (a Kafka-disabled deployment is a
  /// legitimate mode, not a degraded one — matches the Go side's own
  /// documented reasoning).
  /// </summary>
  public void Validate()
  {
    if (!Enabled)
    {
      return;
    }
    if (Brokers.Count == 0)
    {
      throw new ConfigurationV3Error("transport.kafka.enabled=true requires at least one broker");
    }
    for (var i = 0; i < Brokers.Count; i++)
    {
      if (string.IsNullOrWhiteSpace(Brokers[i]))
      {
        throw new ConfigurationV3Error($"transport.kafka.brokers[{i}] is empty");
      }
    }
    if (string.IsNullOrWhiteSpace(ClientId))
    {
      throw new ConfigurationV3Error("transport.kafka.client_id.ctrader_engine is required");
    }
    foreach (var c in ClientId)
    {
      if (c <= ' ' || c == '/' || c > '~')
      {
        throw new ConfigurationV3Error($"transport.kafka.client_id.ctrader_engine \"{ClientId}\" contains an invalid character");
      }
    }
    if (string.IsNullOrWhiteSpace(MarketBarClosedTopic))
    {
      throw new ConfigurationV3Error("transport.kafka.topics.market_bar_closed is required");
    }
  }

  private static bool RequireBool(ConfigDocument doc, string path) =>
    doc.Get(path) as bool?
    ?? throw new ConfigurationV3Error($"missing or non-boolean required config \"{path}\"");

  private static string RequireString(ConfigDocument doc, string path) =>
    doc.Get(path) as string
    ?? throw new ConfigurationV3Error($"missing or non-string required config \"{path}\"");
}
