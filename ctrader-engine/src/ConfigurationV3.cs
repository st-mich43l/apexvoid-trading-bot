namespace ApexVoid.CTraderFeed;

/// <summary>
/// Configuration V3 direct reader for .NET — Stage C5 of
/// docs/configuration-v3-migration-audit.md. Reads config/apexvoid.yml
/// (or config/apexvoid.demo-eval.yml) and its categorized includes plus
/// one environment overlay, following the exact same §14 include/merge/
/// overlay spec as <c>algo-bot/app/configuration/v3_root.py</c> and
/// <c>analysis-engine/internal/config/v3_document.go</c> — the third of
/// three independent implementations of one spec (cross-language parity,
/// §38).
///
/// <para><b>This module is a bounded proof of pattern, not a cutover.</b>
/// Unlike Stage C3 (Python, fully wired live for local/dev) and Stage C4
/// (Go, fully wired — the old manifest reader was deleted, not kept
/// alongside this one), this .NET reader is deliberately NOT wired into
/// <see cref="AutoTradeOptions"/>, <see cref="ResolvedRuntimeManifest"/>,
/// or <see cref="ManifestRuntimeFactory"/>. Those still read the compiled
/// JSON manifest / environment variables exactly as before, and that path
/// is what places real orders. Reasons this stays unwired for now:</para>
/// <list type="bullet">
/// <item><description><c>ResolvedRuntimeManifest</c>/<c>ResolvedAutoTradeProjection</c>
/// carry well over a hundred fields feeding real order-execution decisions
/// (stop distances, sizing, scale-in/add policy, spread guards, ...).
/// Python's cutover (Stage C3) earned trust with an exact 890/890-leaf
/// parity test against the real Pydantic model via the real resolver
/// pipeline. Reaching that same rigor for this surface, in C#, without a
/// reflection-based deserializer (AOT forbids one — see
/// <c>ResolvedRuntimeManifestLoader</c>'s own comment on the exact crash:
/// "Reflection-based serialization has been disabled"), is a
/// multi-hundred-field hand-mapping exercise on its own, not something to
/// rush through to hit a stage count.</description></item>
/// <item><description>This is the one runtime in the whole migration that
/// executes real broker orders with real money. §41 of
/// rebuild-configuration-architecture.md is explicit that safety on this
/// path outranks finishing the stage sequence.</description></item>
/// </list>
/// <para>What this module DOES prove, with real tests against the real
/// config files: the include graph resolves correctly, the environment
/// overlay applies last and deep-merges only mappings, and a geometry read
/// (<see cref="ConfigDocument.GeometryFor"/>) — mirroring Go's narrowly-
/// scoped proof exactly — produces the same pip_size/price_digits every
/// other language reads for every live instrument. That is the honest
/// scope of "Stage C5 done": the reader exists and is proven, wiring it to
/// replace the live path is future work, not done here.</para>
/// </summary>
public static class ConfigurationV3
{
  /// <summary>
  /// The one non-secret configuration bootstrap environment variable
  /// (rebuild-configuration-architecture.md §12) — the same name Python's
  /// <c>CONFIG_FILE_ENV</c>, Go's <c>config.RootFileEnv</c>, and this
  /// repo's docker-compose.yml already use. Not read by anything live in
  /// this project yet (see the module-level remarks above).
  /// </summary>
  public const string RootFileEnv = "APEXVOID_CONFIG_FILE";
}

/// <summary>
/// A resolved Configuration V3 document: every included category's data,
/// deep-merged, with the environment overlay applied last — addressable
/// by dotted path, mirroring Go's <c>config.Document</c>.
/// </summary>
public sealed class ConfigDocument
{
  private readonly IReadOnlyDictionary<string, object?> _raw;

  private ConfigDocument(IReadOnlyDictionary<string, object?> raw)
  {
    _raw = raw;
  }

  /// <summary>
  /// The full resolved document tree. Internal — exposed only for the
  /// Stage C6 cross-language fixture parity test
  /// (<c>ConfigurationV3Tests.ResolveDocumentMatchesCanonicalFixture</c>,
  /// via <c>InternalsVisibleTo("CTraderFeed.Tests")</c>), which is the one
  /// legitimate reason to compare the whole document rather than reading
  /// specific paths through <see cref="Get"/>/<see cref="Section"/>.
  /// </summary>
  internal IReadOnlyDictionary<string, object?> Raw => _raw;

  /// <summary>
  /// Reads a V3 root file (config/apexvoid.yml-shaped: a <c>version: 3</c>
  /// document with an <c>includes:</c> list) and resolves its include
  /// graph plus environment overlay, exactly mirroring
  /// <c>app/configuration/v3_root.py::resolve_v3_document</c> and
  /// <c>analysis-engine/internal/config/v3_document.go::ResolveDocument</c>.
  /// </summary>
  public static ConfigDocument Resolve(string rootPath)
  {
    var root = LoadYaml(rootPath);
    if (root is not Dictionary<string, object?> rootMap)
    {
      throw new ConfigurationV3Error($"{rootPath}: top-level V3 root document must be a mapping");
    }
    if (rootMap.GetValueOrDefault("version") is not long version || version != 3)
    {
      throw new ConfigurationV3Error(
        $"{rootPath}: unsupported version {rootMap.GetValueOrDefault("version")} — only 3 is supported (§18)");
    }
    if (rootMap.GetValueOrDefault("includes") is not List<object?> includesRaw || includesRaw.Count == 0)
    {
      throw new ConfigurationV3Error($"{rootPath}: 'includes' must be a non-empty list");
    }

    var baseDir = Path.GetDirectoryName(Path.GetFullPath(rootPath)) ?? ".";
    var merged = new Dictionary<string, object?> { ["version"] = 3L };
    var seen = new HashSet<string>(StringComparer.Ordinal);
    Dictionary<string, object?>? overlay = null;
    string? environment = null;

    foreach (var item in includesRaw)
    {
      if (item is not string include || include.Length == 0)
      {
        throw new ConfigurationV3Error($"{rootPath}: invalid include entry {item}");
      }
      if (!seen.Add(include))
      {
        throw new ConfigurationV3Error($"{rootPath}: duplicate include \"{include}\"");
      }
      if (include.StartsWith('/') || include.Contains(".."))
      {
        throw new ConfigurationV3Error($"{rootPath}: include \"{include}\" escapes the config root — not allowed");
      }
      var includePath = Path.Combine(baseDir, include);
      if (!File.Exists(includePath))
      {
        throw new ConfigurationV3Error($"{rootPath}: missing include \"{include}\"");
      }

      var doc = LoadYaml(includePath);
      var docMap = doc as Dictionary<string, object?> ?? new Dictionary<string, object?>();

      if (include.StartsWith("environments/", StringComparison.Ordinal))
      {
        if (overlay is not null)
        {
          throw new ConfigurationV3Error($"{rootPath}: more than one environments/*.yml include");
        }
        overlay = docMap;
        environment = Path.GetFileNameWithoutExtension(include);
        continue;
      }

      foreach (var (topKey, value) in docMap)
      {
        if (topKey == "version")
        {
          continue;
        }
        if (merged.ContainsKey(topKey))
        {
          throw new ConfigurationV3Error(
            $"{rootPath}: duplicate base ownership of top-level key \"{topKey}\" (already set before \"{include}\" was included)");
        }
        merged[topKey] = value;
      }
    }

    if (overlay is not null)
    {
      merged = (Dictionary<string, object?>)DeepMerge(merged, overlay)!;
    }
    if (merged.GetValueOrDefault("runtime") is not Dictionary<string, object?> runtimeSection)
    {
      runtimeSection = new Dictionary<string, object?>();
      merged["runtime"] = runtimeSection;
    }
    runtimeSection["environment"] = environment ?? "production";

    return new ConfigDocument(merged);
  }

  private static object? LoadYaml(string path)
  {
    string text;
    try
    {
      text = File.ReadAllText(path);
    }
    catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
    {
      throw new ConfigurationV3Error($"reading {path}: {ex.Message}", ex);
    }
    try
    {
      return MinimalYamlParser.Parse(text);
    }
    catch (Exception ex)
    {
      throw new ConfigurationV3Error($"parsing {path}: {ex.Message}", ex);
    }
  }

  // Implements §14 exactly: mappings deep-merge; scalars and lists are
  // replaced wholesale by the overlay, never concatenated.
  private static object? DeepMerge(object? baseValue, object? overlayValue)
  {
    if (baseValue is not Dictionary<string, object?> baseMap
        || overlayValue is not Dictionary<string, object?> overlayMap)
    {
      return overlayValue;
    }
    var result = new Dictionary<string, object?>(baseMap);
    foreach (var (key, overlayItem) in overlayMap)
    {
      if (result.TryGetValue(key, out var baseItem)
          && baseItem is Dictionary<string, object?>
          && overlayItem is Dictionary<string, object?>)
      {
        result[key] = DeepMerge(baseItem, overlayItem);
      }
      else
      {
        result[key] = overlayItem;
      }
    }
    return result;
  }

  /// <summary>
  /// Walks a dotted path (e.g. "instruments.instruments.XAU.contract.
  /// pip_size") through the resolved document. Returns null if any
  /// segment along the way doesn't exist or isn't a mapping.
  /// </summary>
  public object? Get(string dottedPath)
  {
    object? cursor = _raw;
    foreach (var part in dottedPath.Split('.'))
    {
      if (cursor is not IReadOnlyDictionary<string, object?> map || !map.TryGetValue(part, out cursor))
      {
        return null;
      }
    }
    return cursor;
  }

  /// <summary>
  /// Returns the mapping at dottedPath, or throws if it's missing or not
  /// a mapping — the C# analogue of §9's "a missing required config value
  /// fails, zero/null is never treated as a default."
  /// </summary>
  public IReadOnlyDictionary<string, object?> Section(string dottedPath)
  {
    var value = Get(dottedPath);
    if (value is null)
    {
      throw new ConfigurationV3Error($"missing required section \"{dottedPath}\"");
    }
    if (value is not IReadOnlyDictionary<string, object?> map)
    {
      throw new ConfigurationV3Error($"\"{dottedPath}\" is not a mapping (got {value.GetType()})");
    }
    return map;
  }

  /// <summary>
  /// Builds a <see cref="ConfigInstrumentGeometry"/> for symbol directly
  /// from the resolved document's instruments.yml content —
  /// instruments.instruments.&lt;symbol&gt;, with instrument_packs.
  /// &lt;pack&gt; merged underneath it when the instrument declares one
  /// (the instrument's own leaves always win on a shared key, exactly
  /// matching instrument_packs.py's own merge rule). Fails closed for an
  /// unknown symbol or missing/invalid geometry — §12: no hardcoded pip
  /// size, ever, for any instrument. Mirrors Go's <c>Document.GeometryFor</c>.
  /// </summary>
  public ConfigInstrumentGeometry GeometryFor(string symbol)
  {
    var instruments = Section("instruments");
    if (instruments.GetValueOrDefault(symbol) is not Dictionary<string, object?> instrument)
    {
      throw new ConfigurationV3Error($"unknown instrument \"{symbol}\"");
    }

    var merged = instrument;
    if (instrument.GetValueOrDefault("pack") is string packName && packName.Length > 0)
    {
      var packs = Section("instrument_packs");
      if (packs.GetValueOrDefault(packName) is not Dictionary<string, object?> pack)
      {
        throw new ConfigurationV3Error($"instrument \"{symbol}\" references unknown pack \"{packName}\"");
      }
      merged = (Dictionary<string, object?>)DeepMerge(pack, instrument)!;
    }

    if (merged.GetValueOrDefault("contract") is not Dictionary<string, object?> contract)
    {
      throw new ConfigurationV3Error($"instrument \"{symbol}\" has no contract geometry (from instrument or pack)");
    }
    var pipSize = NumberField(contract, "pip_size", symbol);
    var digits = NumberField(contract, "price_digits", symbol);

    var canonicalSymbol = merged.GetValueOrDefault("canonical_symbol") as string;
    if (string.IsNullOrEmpty(canonicalSymbol))
    {
      canonicalSymbol = symbol;
    }
    var brokerSymbol = merged.GetValueOrDefault("broker_symbol") as string ?? "";

    if (pipSize <= 0)
    {
      throw new ConfigurationV3Error($"instrument \"{symbol}\": pip_size must be positive, got {pipSize}");
    }
    if (digits < 0 || digits != Math.Floor(digits))
    {
      throw new ConfigurationV3Error($"instrument \"{symbol}\": price_digits must be a non-negative integer, got {digits}");
    }

    return new ConfigInstrumentGeometry(canonicalSymbol, brokerSymbol, pipSize, (int)digits);
  }

  /// <summary>
  /// Returns every instrument declared with <c>rollout: live</c> — the
  /// single source Configuration V3 wants for "which symbols are live"
  /// (rebuild-configuration-architecture.md §3), never a second,
  /// separately maintained list. Mirrors Go's <c>Document.LiveInstruments</c>.
  /// </summary>
  public IReadOnlyList<string> LiveInstruments()
  {
    var instruments = Section("instruments");
    var live = new List<string>();
    foreach (var (symbol, raw) in instruments)
    {
      if (raw is Dictionary<string, object?> instrument
          && instrument.GetValueOrDefault("rollout") as string == "live")
      {
        live.Add(symbol);
      }
    }
    live.Sort(StringComparer.Ordinal);
    return live;
  }

  // Reads a required numeric leaf as double, regardless of whether the
  // parser produced long or double for it (MinimalYamlParser.ParseUnquotedScalar
  // tries long first, then double) — a missing or non-numeric field is an
  // error, never a silent zero (§9).
  private static double NumberField(IReadOnlyDictionary<string, object?> map, string key, string symbol)
  {
    if (!map.TryGetValue(key, out var value) || value is null)
    {
      throw new ConfigurationV3Error($"instrument \"{symbol}\": missing required field \"{key}\"");
    }
    return value switch
    {
      long l => l,
      double d => d,
      _ => throw new ConfigurationV3Error($"instrument \"{symbol}\": field \"{key}\" is not numeric (got {value.GetType()})"),
    };
  }
}

/// <summary>
/// Per-instrument market geometry read directly from Configuration V3 —
/// the C# analogue of Go's <c>market.Geometry</c>. Deliberately narrow
/// (the same four fields Go's proof-of-pattern reads), not an attempt to
/// cover the full instrument config surface — see
/// <see cref="ConfigurationV3"/>'s remarks on scope.
/// </summary>
public sealed record ConfigInstrumentGeometry(
  string CanonicalSymbol,
  string BrokerSymbol,
  double PipSize,
  int PriceDigits
);

/// <summary>
/// Raised for any Configuration V3 resolution failure — malformed YAML,
/// a spec violation (duplicate/escaping/missing include, duplicate
/// top-level ownership, more than one environment overlay), or a missing/
/// invalid required field. Never caught and defaulted; every caller either
/// propagates it or fails startup, matching §9's "fail rather than
/// silently default" rule.
/// </summary>
public sealed class ConfigurationV3Error : Exception
{
  public ConfigurationV3Error(string message) : base($"config: {message}")
  {
  }

  public ConfigurationV3Error(string message, Exception inner) : base($"config: {message}", inner)
  {
  }
}
