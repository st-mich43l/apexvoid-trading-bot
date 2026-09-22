using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexVoid.CTraderFeed.Transport.Kafka;

/// <summary>
/// Source-generated <see cref="JsonSerializerContext"/> for every Kafka
/// event type this service produces — the same
/// <c>[JsonSerializable]</c>+snake_case pattern
/// <see cref="RedisJsonContext"/> already establishes elsewhere in this
/// project (RedisBarSink.cs), reused here rather than reinvented: Native
/// AOT forbids the reflection-based <see cref="JsonSerializer"/> default
/// path (the same class of problem ADR-010 found and worked around for
/// the Kafka client itself — <see cref="System.Text.Json"/>'s source
/// generator is the officially sanctioned, well-tested AOT-safe
/// alternative, unlike Confluent.Kafka's undocumented internal
/// reflection). Field names match
/// contracts/common/event-envelope-v1.schema.json and
/// contracts/market/bar-closed-v1.schema.json exactly via
/// <see cref="JsonKnownNamingPolicy.SnakeCaseLower"/> — no per-property
/// <c>[JsonPropertyName]</c> overrides needed since every C# property
/// name here is already the exact PascalCase form of its snake_case JSON
/// name.
/// </summary>
[JsonSourceGenerationOptions(
  DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
  PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower
)]
[JsonSerializable(typeof(EventEnvelope<BarClosedPayload>))]
[JsonSerializable(typeof(BarClosedPayload))]
internal sealed partial class EventJsonContext : JsonSerializerContext
{
}

/// <summary>
/// Config provenance (source task §13/§19): which exact ApexVoid
/// configuration produced this event, answerable historically. The .NET
/// mirror of analysis-engine's
/// <c>internal/engine.ConfigProvenanceFromConfig</c> — same two-part
/// shape (Configuration V3's own version marker + a content hash of the
/// resolved document), computed once at startup, never per event.
/// Cross-language byte-identical hashes were never a goal (each
/// language's own JSON serialization details differ even over
/// semantically identical content) — what matters is that each
/// language's own fingerprint is a real, deterministic function of its
/// own resolved configuration, not a placeholder.
/// </summary>
public sealed record KafkaConfigProvenance(int Version, string Fingerprint)
{
  public static KafkaConfigProvenance FromConfig(ConfigDocument doc)
  {
    var version = doc.Get("version") as long?
      ?? throw new ConfigurationV3Error("resolved document missing its own \"version\" marker");
    var canonical = CanonicalJson.Serialize(doc.Raw);
    var hash = SHA256.HashData(Encoding.UTF8.GetBytes(canonical));
    // Convert.ToHexStringLower is .NET 9+; this project targets net8.0.
    var fingerprint = Convert.ToHexString(hash.AsSpan(0, 16)).ToLowerInvariant();
    return new KafkaConfigProvenance((int)version, fingerprint);
  }
}

/// <summary>
/// A minimal, deterministic (keys sorted recursively) JSON dump of a
/// resolved Configuration V3 tree — used only to compute
/// <see cref="KafkaConfigProvenance"/>'s content hash. Deliberately not
/// <see cref="JsonSerializer"/> (that would need a reflection-based
/// <c>object?</c>/<c>Dictionary&lt;string,object?&gt;</c> converter,
/// exactly what Native AOT forbids without a source generator, and a
/// generic "serialize any object graph" source-generator registration
/// isn't practical here) — a small hand-written writer over the same
/// <c>Dictionary&lt;string,object?&gt;</c>/<c>List&lt;object?&gt;</c>/
/// scalar shape <see cref="MinimalYamlParser"/> already produces.
/// </summary>
internal static class CanonicalJson
{
  public static string Serialize(object? value)
  {
    var sb = new StringBuilder();
    Write(sb, value);
    return sb.ToString();
  }

  private static void Write(StringBuilder sb, object? value)
  {
    switch (value)
    {
      case null:
        sb.Append("null");
        break;
      case bool b:
        sb.Append(b ? "true" : "false");
        break;
      case long l:
        sb.Append(l.ToString(System.Globalization.CultureInfo.InvariantCulture));
        break;
      case double d:
        sb.Append(d.ToString("R", System.Globalization.CultureInfo.InvariantCulture));
        break;
      case string s:
        WriteString(sb, s);
        break;
      case IReadOnlyDictionary<string, object?> map:
        sb.Append('{');
        var first = true;
        foreach (var key in map.Keys.OrderBy(k => k, StringComparer.Ordinal))
        {
          if (!first)
          {
            sb.Append(',');
          }
          first = false;
          WriteString(sb, key);
          sb.Append(':');
          Write(sb, map[key]);
        }
        sb.Append('}');
        break;
      case System.Collections.IEnumerable list:
        sb.Append('[');
        var firstItem = true;
        foreach (var item in list)
        {
          if (!firstItem)
          {
            sb.Append(',');
          }
          firstItem = false;
          Write(sb, item);
        }
        sb.Append(']');
        break;
      default:
        throw new InvalidOperationException($"CanonicalJson: unsupported value type {value.GetType()}");
    }
  }

  private static void WriteString(StringBuilder sb, string s)
  {
    sb.Append('"');
    foreach (var c in s)
    {
      switch (c)
      {
        case '"': sb.Append("\\\""); break;
        case '\\': sb.Append("\\\\"); break;
        case '\n': sb.Append("\\n"); break;
        case '\r': sb.Append("\\r"); break;
        case '\t': sb.Append("\\t"); break;
        default:
          if (c < 0x20)
          {
            sb.Append("\\u").Append(((int)c).ToString("x4"));
          }
          else
          {
            sb.Append(c);
          }
          break;
      }
    }
    sb.Append('"');
  }
}
