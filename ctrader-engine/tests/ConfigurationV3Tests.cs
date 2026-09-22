using System.Text.Json;
using Xunit;

namespace CTraderFeed.Tests;

/// <summary>
/// Tests for the Stage C5 Configuration V3 direct reader
/// (<c>src/MinimalYamlParser.cs</c> + <c>src/ConfigurationV3.cs</c>).
/// Mirrors <c>analysis-engine/test/config/v3_document_test.go</c>
/// test-for-test where the assertion is about the shared §14 spec (three
/// independent implementations proving the same behavior), plus
/// standalone <see cref="MinimalYamlParser"/> tests for the hand-rolled
/// parsing this project alone needed (Go and Python both had real YAML
/// libraries available).
/// </summary>
public sealed class ConfigurationV3Tests
{
  // Mirrors ResolvedRuntimeManifestTests.FixturePath()'s exact approach:
  // AppContext.BaseDirectory is .../tests/bin/<Config>/net8.0/ at test run
  // time, five levels above the repo root.
  private static string RepoConfigPath(params string[] parts)
  {
    var root = Path.GetFullPath(
      Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..")
    );
    return Path.Combine([root, "config", .. parts]);
  }

  // --- ConfigDocument.Resolve against the real repository config files ---

  [Theory]
  [InlineData("XAU", 0.1, 2)]
  [InlineData("EURUSD", 0.0001, 5)]
  [InlineData("GBPUSD", 0.0001, 5)]
  [InlineData("GBPJPY", 0.01, 3)]
  [InlineData("USDJPY", 0.01, 3)]
  public void ResolveDocumentProductionMatchesKnownGeometry(string symbol, double pipSize, int priceDigits)
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));
    var geometry = doc.GeometryFor(symbol);
    Assert.Equal(pipSize, geometry.PipSize);
    Assert.Equal(priceDigits, geometry.PriceDigits);
  }

  [Fact]
  public void ResolveDocumentUnknownInstrumentFailsClosed()
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));
    Assert.Throws<ConfigurationV3Error>(() => doc.GeometryFor("DOGEUSD"));
  }

  [Fact]
  public void LiveInstrumentsMatchesEveryDeclaredLiveSymbol()
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));
    var live = doc.LiveInstruments();
    var want = new[] { "EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU" };
    Assert.Equal(want, live);
  }

  [Fact]
  public void ResolveDemoEvalRootUsesDemoEvalOverlay()
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.demo-eval.yml"));
    Assert.Equal("demo_eval", doc.Get("runtime.environment"));
    // Instrument geometry is untouched by the environment overlay — same
    // XAU pip size either way.
    Assert.Equal(0.1, doc.GeometryFor("XAU").PipSize);
  }

  [Fact]
  public void ResolveDocumentProductionOverlayIsEmpty()
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));
    Assert.Equal("production", doc.Get("runtime.environment"));
  }

  [Fact]
  public void SectionFailsClosedOnMissingOrWrongType()
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));
    Assert.Throws<ConfigurationV3Error>(() => doc.Section("does.not.exist"));
    // runtime.environment is a scalar string, not a mapping.
    Assert.Throws<ConfigurationV3Error>(() => doc.Section("runtime.environment"));
  }

  // --- ResolveDocument spec-conformance against synthetic temp fixtures,
  // matching the Go test suite's own synthetic-fixture cases exactly ---

  private static string WriteTempConfig(string dir, string name, string content)
  {
    var path = Path.Combine(dir, name);
    Directory.CreateDirectory(Path.GetDirectoryName(path)!);
    File.WriteAllText(path, content);
    return path;
  }

  [Fact]
  public void ResolveDocumentRejectsUnsupportedVersion()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    var root = WriteTempConfig(dir, "apexvoid.yml", "version: 2\nincludes: []\n");
    var ex = Assert.Throws<ConfigurationV3Error>(() => ConfigDocument.Resolve(root));
    Assert.Contains("unsupported version", ex.Message);
  }

  [Fact]
  public void ResolveDocumentRejectsMissingInclude()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    var root = WriteTempConfig(dir, "apexvoid.yml", "version: 3\nincludes:\n  - does-not-exist.yml\n");
    var ex = Assert.Throws<ConfigurationV3Error>(() => ConfigDocument.Resolve(root));
    Assert.Contains("missing include", ex.Message);
  }

  [Fact]
  public void ResolveDocumentRejectsDuplicateInclude()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    WriteTempConfig(dir, "a.yml", "foo:\n  bar: 1\n");
    var root = WriteTempConfig(dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n  - a.yml\n");
    var ex = Assert.Throws<ConfigurationV3Error>(() => ConfigDocument.Resolve(root));
    Assert.Contains("duplicate include", ex.Message);
  }

  [Fact]
  public void ResolveDocumentRejectsEscapingInclude()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    var root = WriteTempConfig(dir, "apexvoid.yml", "version: 3\nincludes:\n  - ../outside.yml\n");
    var ex = Assert.Throws<ConfigurationV3Error>(() => ConfigDocument.Resolve(root));
    Assert.Contains("escapes", ex.Message);
  }

  [Fact]
  public void ResolveDocumentRejectsDuplicateBaseOwnership()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    WriteTempConfig(dir, "a.yml", "foo:\n  bar: 1\n");
    WriteTempConfig(dir, "b.yml", "foo:\n  baz: 2\n");
    var root = WriteTempConfig(dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n  - b.yml\n");
    var ex = Assert.Throws<ConfigurationV3Error>(() => ConfigDocument.Resolve(root));
    Assert.Contains("duplicate base ownership", ex.Message);
  }

  [Fact]
  public void ResolveDocumentOverlayReplacesScalarAndExtendsMap()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    WriteTempConfig(dir, "a.yml", "foo:\n  bar: 1\n  baz: 2\n");
    WriteTempConfig(dir, Path.Combine("environments", "test.yml"), "foo:\n  bar: 99\n");
    var root = WriteTempConfig(dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n  - environments/test.yml\n");

    var doc = ConfigDocument.Resolve(root);
    Assert.Equal(99L, doc.Get("foo.bar"));
    Assert.Equal(2L, doc.Get("foo.baz"));
    Assert.Equal("test", doc.Get("runtime.environment"));
  }

  [Fact]
  public void ResolveDocumentRejectsMoreThanOneEnvironmentOverlay()
  {
    var dir = Directory.CreateTempSubdirectory().FullName;
    WriteTempConfig(dir, Path.Combine("environments", "a.yml"), "foo: 1\n");
    WriteTempConfig(dir, Path.Combine("environments", "b.yml"), "foo: 2\n");
    var root = WriteTempConfig(
      dir, "apexvoid.yml", "version: 3\nincludes:\n  - environments/a.yml\n  - environments/b.yml\n");
    var ex = Assert.Throws<ConfigurationV3Error>(() => ConfigDocument.Resolve(root));
    Assert.Contains("more than one environments/*.yml include", ex.Message);
  }

  // --- MinimalYamlParser: the hand-rolled subset this project alone needed ---

  [Fact]
  public void ParsesNestedMappings()
  {
    var result = MinimalYamlParser.Parse("a:\n  b:\n    c: 1\n  d: 2\n");
    var a = Assert.IsType<Dictionary<string, object?>>(result);
    var b = Assert.IsType<Dictionary<string, object?>>(a["a"]);
    var c = Assert.IsType<Dictionary<string, object?>>(b["b"]);
    Assert.Equal(1L, c["c"]);
    Assert.Equal(2L, b["d"]);
  }

  [Fact]
  public void ParsesBlockSequenceOfScalars()
  {
    var result = MinimalYamlParser.Parse("items:\n  - H1\n  - M15\n  - M5\n");
    var root = Assert.IsType<Dictionary<string, object?>>(result);
    var items = Assert.IsType<List<object?>>(root["items"]);
    Assert.Equal(["H1", "M15", "M5"], items);
  }

  [Fact]
  public void ParsesBlockSequenceOfMappings()
  {
    var result = MinimalYamlParser.Parse("items:\n  - name: a\n    value: 1\n  - name: b\n    value: 2\n");
    var root = Assert.IsType<Dictionary<string, object?>>(result);
    var items = Assert.IsType<List<object?>>(root["items"]);
    Assert.Equal(2, items.Count);
    var first = Assert.IsType<Dictionary<string, object?>>(items[0]);
    Assert.Equal("a", first["name"]);
    Assert.Equal(1L, first["value"]);
  }

  [Fact]
  public void ParsesInlineFlowSequence()
  {
    // The exact shape confirmed present in the real repo config (e.g.
    // instrument_packs.*.manual.target_close_ratios: [0.25, 0.25, 0.50]).
    var result = MinimalYamlParser.Parse("target_close_ratios: [0.25, 0.25, 0.50]\n");
    var root = Assert.IsType<Dictionary<string, object?>>(result);
    var ratios = Assert.IsType<List<object?>>(root["target_close_ratios"]);
    Assert.Equal([0.25, 0.25, 0.50], ratios);
  }

  [Fact]
  public void ParsesEmptyInlineFlowSequence()
  {
    var result = MinimalYamlParser.Parse("items: []\n");
    var root = Assert.IsType<Dictionary<string, object?>>(result);
    var items = Assert.IsType<List<object?>>(root["items"]);
    Assert.Empty(items);
  }

  [Fact]
  public void ParsesScalarTypesAndComments()
  {
    var result = MinimalYamlParser.Parse(
      "# a leading comment\n" +
      "str_val: hello world  # trailing comment\n" +
      "int_val: 42\n" +
      "float_val: 0.0018\n" +
      "bool_true: true\n" +
      "bool_false: false\n" +
      "null_val: null\n" +
      "quoted: \"a: b # not a comment\"\n"
    );
    var root = Assert.IsType<Dictionary<string, object?>>(result);
    Assert.Equal("hello world", root["str_val"]);
    Assert.Equal(42L, root["int_val"]);
    Assert.Equal(0.0018, root["float_val"]);
    Assert.Equal(true, root["bool_true"]);
    Assert.Equal(false, root["bool_false"]);
    Assert.Null(root["null_val"]);
    Assert.Equal("a: b # not a comment", root["quoted"]);
  }

  [Fact]
  public void ParsesRealInstrumentsFileWithoutThrowing()
  {
    // The strongest available test for the parser's real-world coverage:
    // parse the actual, real config/instruments.yml (largest and most
    // structurally varied file in config/) and confirm the specific
    // known-good values round-trip, rather than only synthetic snippets.
    var text = File.ReadAllText(RepoConfigPath("instruments.yml"));
    var result = MinimalYamlParser.Parse(text);
    var root = Assert.IsType<Dictionary<string, object?>>(result);
    var packs = Assert.IsType<Dictionary<string, object?>>(root["instrument_packs"]);
    var fxPack = Assert.IsType<Dictionary<string, object?>>(packs["fx_usd_major_fixed_2r_v1"]);
    var manual = Assert.IsType<Dictionary<string, object?>>(fxPack["manual"]);
    var ratios = Assert.IsType<List<object?>>(manual["target_close_ratios"]);
    Assert.Equal([0.25, 0.25, 0.50], ratios);
    var instruments = Assert.IsType<Dictionary<string, object?>>(root["instruments"]);
    var xau = Assert.IsType<Dictionary<string, object?>>(instruments["XAU"]);
    Assert.Equal("live", xau["rollout"]);
  }

  // --- Stage C6: cross-language parity against the shared canonical
  // fixture. contracts/configuration/examples/resolved-production-v3.json
  // is generated by config/scripts/resolve_reference.py — a fourth,
  // minimal reference implementation of §14 whose only job is producing
  // this fixture and validating it against the JSON Schema. §38 requires
  // every real per-language V3 reader (this reader, Go's ResolveDocument,
  // Python's v3_root.resolve_v3_document) to reproduce this exact
  // document when resolving the real config/apexvoid.yml for the
  // production environment. This is Go's counterpart to
  // v3_document_test.go's TestResolveDocumentMatchesCanonicalFixture and
  // Python's test_config_v3_cross_language_fixture.py.

  private static string FixturePath() => Path.Combine(
    Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..")),
    "contracts", "configuration", "examples", "resolved-production-v3.json"
  );

  // Converts a parsed JsonElement into the same generic tree shape
  // MinimalYamlParser produces (Dictionary<string, object?>/List<object?>/
  // string/double/bool/null), so the fixture and the resolved document can
  // be compared structurally without reflection.
  private static object? JsonToTree(JsonElement element) => element.ValueKind switch
  {
    JsonValueKind.Object => element.EnumerateObject()
      .ToDictionary(p => p.Name, p => JsonToTree(p.Value)),
    JsonValueKind.Array => element.EnumerateArray().Select(JsonToTree).ToList(),
    JsonValueKind.String => element.GetString(),
    JsonValueKind.Number => element.GetDouble(),
    JsonValueKind.True => true,
    JsonValueKind.False => false,
    JsonValueKind.Null => null,
    _ => throw new InvalidOperationException($"unexpected JSON value kind {element.ValueKind}"),
  };

  // Recursively converts every `long` (MinimalYamlParser's decoding for a
  // bare integer literal) to `double`, matching what JsonToTree always
  // produces for a JSON number — without this, a value that's
  // semantically identical (3 vs 3.0) would spuriously fail equality
  // purely because the YAML and JSON sides disagree on which numeric type
  // represents "a whole number with no decimal point in the source." The
  // Go parity test has the identical normalization step for the identical
  // reason; the Python one doesn't need it because Python's `3 == 3.0` is
  // already True.
  private static object? NormalizeNumbers(object? node) => node switch
  {
    Dictionary<string, object?> map => map.ToDictionary(kv => kv.Key, kv => NormalizeNumbers(kv.Value)),
    List<object?> list => list.Select(NormalizeNumbers).ToList(),
    long l => (double)l,
    var other => other,
  };

  // Structural equality over the normalized tree shape — Dictionary/List
  // don't get value-equality semantics for free from Assert.Equal in the
  // way a record or array would, so this walks explicitly.
  private static bool DeepEqual(object? a, object? b)
  {
    if (a is Dictionary<string, object?> mapA && b is Dictionary<string, object?> mapB)
    {
      return mapA.Count == mapB.Count
        && mapA.All(kv => mapB.TryGetValue(kv.Key, out var other) && DeepEqual(kv.Value, other));
    }
    if (a is List<object?> listA && b is List<object?> listB)
    {
      return listA.Count == listB.Count && listA.Zip(listB, DeepEqual).All(x => x);
    }
    return Equals(a, b);
  }

  [Fact]
  public void ResolveDocumentMatchesCanonicalFixture()
  {
    var doc = ConfigDocument.Resolve(RepoConfigPath("apexvoid.yml"));
    using var fixtureStream = File.OpenRead(FixturePath());
    using var fixtureJson = JsonDocument.Parse(fixtureStream);

    var got = NormalizeNumbers(doc.Raw);
    var want = NormalizeNumbers(JsonToTree(fixtureJson.RootElement));

    Assert.True(
      DeepEqual(got, want),
      "ConfigDocument.Resolve(config/apexvoid.yml) no longer matches "
        + "contracts/configuration/examples/resolved-production-v3.json. "
        + "Either config/*.yml changed without regenerating the fixture "
        + "(algo-bot/.venv/bin/python config/scripts/resolve_reference.py "
        + "--environment production --write-fixture) or ConfigDocument's "
        + "resolution logic diverged from resolve_reference.py's — both "
        + "must implement §14 identically."
    );
  }

  [Fact]
  public void FixtureDeclaresProductionEnvironment()
  {
    using var fixtureStream = File.OpenRead(FixturePath());
    using var fixtureJson = JsonDocument.Parse(fixtureStream);
    var environment = fixtureJson.RootElement.GetProperty("runtime").GetProperty("environment").GetString();
    Assert.Equal("production", environment);
  }
}
