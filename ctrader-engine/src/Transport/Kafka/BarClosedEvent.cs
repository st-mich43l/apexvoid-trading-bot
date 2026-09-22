namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// What <see cref="FeedRunner"/> hands to
/// <see cref="IMarketEventPublisher.PublishClosedBarAsync"/> — bundles the
/// symbol/timeframe identity <see cref="OhlcBar"/> alone doesn't carry,
/// plus the trace IDs this specific publish should carry. NOT the wire
/// shape (see <see cref="BarClosedPayload"/> for that) — this is the
/// internal domain-to-transport boundary, the .NET mirror of
/// analysis-engine's own adapter.go role but in the opposite direction
/// (domain -> wire here, wire -> domain there), source task §40's
/// "explicit adapter" applying symmetrically.
/// </summary>
public sealed record ClosedBarEvent(
  string CanonicalSymbol,
  string? BrokerSymbol,
  string Timeframe,
  OhlcBar Bar,
  string CorrelationId,
  string? CausationId = null,
  // True for a reconnect-gap incremental-catchup bar (source task §7) —
  // never for a startup full-window bootstrap bar (those never reach
  // IMarketEventPublisher at all) and never for an ordinary live bar.
  // Carried on the event itself, not a second IMarketEventPublisher
  // method overload, so the interface stays exactly the single-method
  // shape source task §4 asks for.
  bool IsRecovery = false
);

/// <summary>
/// Mirrors contracts/market/bar-closed-v1.schema.json exactly — kept in
/// sync by hand (ADR-007's discipline; no schema-to-struct generator
/// exists yet). This is the PAYLOAD only; it travels inside
/// <c>EventEnvelope&lt;BarClosedPayload&gt;.Payload</c>, never published
/// bare. <see cref="CloseTime"/> is computed via
/// <see cref="OhlcBar.CloseTimestamp"/> — the SAME existing helper
/// <see cref="FeedRunner.BackfillAsync"/> already uses, not a
/// reimplementation (source task §9's bar-identity decision: close time,
/// not <see cref="OhlcBar.Timestamp"/>'s own open-time convention — see
/// that schema file's own note on why this intentionally diverges from
/// today's Redis bars:{SYMBOL}:{TF} scoring).
/// </summary>
public sealed record BarClosedPayload(
  string CanonicalSymbol,
  string? BrokerSymbol,
  string Timeframe,
  long OpenTime,
  long CloseTime,
  decimal Open,
  decimal High,
  decimal Low,
  decimal Close,
  decimal Volume
)
{
  public static BarClosedPayload From(ClosedBarEvent closedBar) => new(
    CanonicalSymbol: closedBar.CanonicalSymbol,
    BrokerSymbol: closedBar.BrokerSymbol,
    Timeframe: closedBar.Timeframe,
    OpenTime: closedBar.Bar.Timestamp,
    CloseTime: closedBar.Bar.CloseTimestamp(closedBar.Timeframe),
    Open: closedBar.Bar.Open,
    High: closedBar.Bar.High,
    Low: closedBar.Bar.Low,
    Close: closedBar.Bar.Close,
    Volume: closedBar.Bar.Volume
  );
}
