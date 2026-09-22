using System.Security.Cryptography;

namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// The shared cross-service event wrapper
/// (contracts/common/event-envelope-v1.schema.json) — the .NET mirror of
/// analysis-engine's <c>internal/transport/kafka.Envelope</c>. Kept in
/// sync with that schema by hand (ADR-007's discipline; no
/// schema-to-struct generator exists yet). Generic over TPayload so each
/// event type (today: <see cref="BarClosedPayload"/>) gets its own
/// closed, source-generator-friendly instantiation — see
/// <see cref="EventJsonContext"/> — rather than a boxed/JsonElement
/// payload, which keeps this fully Native AOT / trim safe without any
/// reflection-based fallback.
/// </summary>
public sealed record EventEnvelope<TPayload>(
  string EventId,
  string EventType,
  int EventVersion,
  long OccurredAt,
  long ProducedAt,
  string Producer,
  string CorrelationId,
  string? CausationId,
  int? ConfigVersion,
  string? ConfigFingerprint,
  TPayload Payload
);

/// <summary>
/// UUIDv7 (source task §10/§20, RFC 9562 §5.7) — hand-rolled to match
/// analysis-engine's own <c>kafka.NewEventID</c> bit layout exactly
/// (48-bit millisecond timestamp, version nibble 0x7, RFC 4122 variant,
/// remaining bits cryptographically random), rather than adding a UUID
/// NuGet dependency. .NET 9+ ships <c>Guid.CreateVersion7()</c> natively,
/// but this project targets net8.0 (matching cTrader.OpenAPI.Net /
/// StackExchange.Redis's own target and the project's existing Native
/// AOT publish pipeline, ADR-010) — not available here yet. Semantically
/// compatible with the Go side's IDs (both are real UUIDv7 values per
/// the RFC), though byte-for-byte identical output was never a goal:
/// source task §10 is explicit that event_id is never used for ordering
/// or deduplication by a consumer, only tracing — cross-language exact
/// match is not load-bearing.
/// </summary>
public static class Uuid7
{
  public static string NewId()
  {
    Span<byte> bytes = stackalloc byte[16];
    var ms = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    bytes[0] = (byte)(ms >> 40);
    bytes[1] = (byte)(ms >> 32);
    bytes[2] = (byte)(ms >> 24);
    bytes[3] = (byte)(ms >> 16);
    bytes[4] = (byte)(ms >> 8);
    bytes[5] = (byte)ms;
    RandomNumberGenerator.Fill(bytes[6..]);
    bytes[6] = (byte)((bytes[6] & 0x0F) | 0x70); // version 7
    bytes[8] = (byte)((bytes[8] & 0x3F) | 0x80); // RFC 4122 variant

    return string.Create(36, bytes.ToArray(), (span, b) =>
    {
      WriteHexGroup(span[..8], b.AsSpan(0, 4));
      span[8] = '-';
      WriteHexGroup(span[9..13], b.AsSpan(4, 2));
      span[13] = '-';
      WriteHexGroup(span[14..18], b.AsSpan(6, 2));
      span[18] = '-';
      WriteHexGroup(span[19..23], b.AsSpan(8, 2));
      span[23] = '-';
      WriteHexGroup(span[24..36], b.AsSpan(10, 6));
    });
  }

  private static void WriteHexGroup(Span<char> destination, ReadOnlySpan<byte> source)
  {
    for (var i = 0; i < source.Length; i++)
    {
      var b = source[i];
      destination[i * 2] = ToHexChar(b >> 4);
      destination[i * 2 + 1] = ToHexChar(b & 0xF);
    }
  }

  private static char ToHexChar(int nibble) =>
    (char)(nibble < 10 ? '0' + nibble : 'a' + (nibble - 10));
}
