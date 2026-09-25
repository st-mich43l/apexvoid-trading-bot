namespace ApexVoid.CTraderFeed;

/// <summary>
/// A hand-rolled parser for the specific YAML subset config/*.yml actually
/// uses: 2-space-indented block mappings and sequences, scalar values
/// (bare/quoted strings, ints, floats, bool, null), flow-style inline
/// lists (<c>[a, b, c]</c>, scalars only, no nested flow mappings), and
/// comments. No anchors/aliases, no block scalars (<c>|</c>/<c>&gt;</c>),
/// no multi-document streams — confirmed absent from every file under
/// config/ (grepped before writing this).
///
/// Written by hand instead of adding a YAML library deliberately:
/// <c>CTraderFeed.csproj</c> publishes Native AOT
/// (<c>&lt;PublishAot&gt;true&lt;/PublishAot&gt;</c>), and reflection-based
/// deserialization is exactly the class of thing that breaks under AOT
/// trimming without careful, easy-to-get-wrong configuration — this
/// project has already hit an AOT-only configuration-loader failure once
/// before that a normal (non-AOT) <c>dotnet test</c> run did not catch
/// (rebuild-configuration-architecture.md §39). A hand-written recursive-
/// descent parser over plain strings/collections has nothing for the
/// trimmer to remove and nothing that needs a source generator.
/// </summary>
public static class MinimalYamlParser
{
  /// <summary>
  /// Parses YAML text into the same generic tree shape the Python
  /// (<c>dict</c>) and Go (<c>map[string]any</c>) V3 readers use:
  /// <c>Dictionary&lt;string, object?&gt;</c> for mappings,
  /// <c>List&lt;object?&gt;</c> for sequences, and <c>string</c>/
  /// <c>long</c>/<c>double</c>/<c>bool</c>/<c>null</c> for scalars.
  /// </summary>
  public static object? Parse(string text)
  {
    var lines = SplitLines(text);
    var index = 0;
    var (value, _) = ParseBlock(lines, ref index, 0);
    return value;
  }

  private sealed record Line(int Indent, string Content, int SourceLineNumber);

  private static List<Line> SplitLines(string text)
  {
    var raw = text.Replace("\r\n", "\n").Split('\n');
    var result = new List<Line>();
    for (var i = 0; i < raw.Length; i++)
    {
      var line = StripComment(raw[i]);
      if (line.Trim().Length == 0)
      {
        continue;
      }
      var indent = 0;
      while (indent < line.Length && line[indent] == ' ')
      {
        indent++;
      }
      result.Add(new Line(indent, line[indent..], i + 1));
    }
    return result;
  }

  // Strips a trailing "# comment" that is not inside a quoted string.
  private static string StripComment(string line)
  {
    var inSingle = false;
    var inDouble = false;
    for (var i = 0; i < line.Length; i++)
    {
      var c = line[i];
      if (c == '\'' && !inDouble)
      {
        inSingle = !inSingle;
      }
      else if (c == '"' && !inSingle)
      {
        inDouble = !inDouble;
      }
      else if (c == '#' && !inSingle && !inDouble)
      {
        if (i == 0 || char.IsWhiteSpace(line[i - 1]))
        {
          return line[..i].TrimEnd();
        }
      }
    }
    return line.TrimEnd();
  }

  // Parses a mapping or sequence block starting at lines[index], where
  // every line at exactly `indent` belongs to this block; returns the
  // parsed value and leaves `index` at the first line outside the block.
  private static (object? value, bool isSequence) ParseBlock(List<Line> lines, ref int index, int indent)
  {
    if (index >= lines.Count || lines[index].Indent < indent)
    {
      return (null, false);
    }
    var blockIndent = lines[index].Indent;
    if (blockIndent < indent)
    {
      return (null, false);
    }

    // A block's value may itself be a single bare flow collection instead
    // of a block mapping/sequence — e.g. config/environments/production.yml
    // is, in its entirety, just "{}" (an intentionally empty environment
    // overlay; see that file's own header comment for why). Valid YAML
    // never mixes a bare flow scalar with sibling block-mapping keys at
    // the same indent, so seeing one here means it IS the whole block.
    if (IsBareFlowScalar(lines[index].Content))
    {
      var flowValue = ParseScalar(lines[index].Content);
      index++;
      return (flowValue, flowValue is List<object?>);
    }

    if (lines[index].Content.StartsWith("- ", StringComparison.Ordinal) || lines[index].Content == "-")
    {
      var sequence = new List<object?>();
      while (index < lines.Count && lines[index].Indent == blockIndent
             && (lines[index].Content.StartsWith("- ", StringComparison.Ordinal) || lines[index].Content == "-"))
      {
        var itemText = lines[index].Content == "-" ? "" : lines[index].Content[2..];
        if (itemText.Length == 0)
        {
          index++;
          var (nested, _) = ParseBlock(lines, ref index, blockIndent + 1);
          sequence.Add(nested);
        }
        else if (LooksLikeMappingEntry(itemText))
        {
          // "- key: value" starts an inline mapping at this item's own
          // effective indent (blockIndent + 2, i.e. past "- ").
          var syntheticIndent = blockIndent + 2;
          lines[index] = new Line(syntheticIndent, itemText, lines[index].SourceLineNumber);
          var (itemMapping, _) = ParseBlock(lines, ref index, syntheticIndent);
          sequence.Add(itemMapping);
        }
        else
        {
          sequence.Add(ParseScalar(itemText));
          index++;
        }
      }
      return (sequence, true);
    }

    var mapping = new Dictionary<string, object?>();
    while (index < lines.Count && lines[index].Indent == blockIndent)
    {
      var content = lines[index].Content;
      var colon = FindKeyColon(content)
        ?? throw new FormatException($"MinimalYamlParser: line {lines[index].SourceLineNumber}: expected 'key: value', got {content!.Trim()!.PadRight(0)}\"{content}\"");
      var key = ParseKey(content[..colon]);
      var rest = content[(colon + 1)..].TrimStart();
      index++;
      if (rest.Length == 0)
      {
        if (index < lines.Count && lines[index].Indent > blockIndent)
        {
          var (nested, _) = ParseBlock(lines, ref index, lines[index].Indent);
          mapping[key] = nested;
        }
        else
        {
          mapping[key] = null;
        }
      }
      else
      {
        mapping[key] = ParseScalar(rest);
      }
    }
    return (mapping, false);
  }

  private static bool LooksLikeMappingEntry(string text) => FindKeyColon(text) is not null;

  // A line is a bare flow collection (not a "key: value" mapping entry)
  // when it opens with '{' or '[' and carries no key-separating colon of
  // its own outside that collection.
  private static bool IsBareFlowScalar(string content) =>
    content.Length > 0 && (content[0] == '{' || content[0] == '[') && FindKeyColon(content) is null;

  // Finds the ": " (or trailing ":") that separates a mapping key from its
  // value, ignoring colons inside quoted strings and inside flow
  // collections ("{...}"/"[...]" — e.g. a "- key: [a: 1]"-shaped line,
  // not that this repo's config actually nests a flow mapping inside a
  // flow sequence, but the depth tracking costs nothing and is exactly
  // what SplitFlowItems already does for the same reason).
  private static int? FindKeyColon(string content)
  {
    var inSingle = false;
    var inDouble = false;
    var depth = 0;
    for (var i = 0; i < content.Length; i++)
    {
      var c = content[i];
      if (c == '\'' && !inDouble)
      {
        inSingle = !inSingle;
      }
      else if (c == '"' && !inSingle)
      {
        inDouble = !inDouble;
      }
      else if (!inSingle && !inDouble)
      {
        if (c is '{' or '[')
        {
          depth++;
        }
        else if (c is '}' or ']')
        {
          depth--;
        }
        else if (c == ':' && depth == 0)
        {
          if (i + 1 == content.Length || content[i + 1] == ' ')
          {
            return i;
          }
        }
      }
    }
    return null;
  }

  private static string ParseKey(string raw)
  {
    raw = raw.Trim();
    if (raw.Length >= 2 && raw[0] == '\'' && raw[^1] == '\'')
    {
      return raw[1..^1];
    }
    if (raw.Length >= 2 && raw[0] == '"' && raw[^1] == '"')
    {
      return raw[1..^1];
    }
    return raw;
  }

  private static object? ParseScalar(string raw)
  {
    raw = raw.Trim();
    if (raw.Length == 0)
    {
      return null;
    }
    if (raw.StartsWith('[') && raw.EndsWith(']'))
    {
      return ParseFlowSequence(raw[1..^1]);
    }
    if (raw.StartsWith('{') && raw.EndsWith('}'))
    {
      return ParseFlowMapping(raw[1..^1]);
    }
    if (raw.Length >= 2 && raw[0] == '\'' && raw[^1] == '\'')
    {
      return raw[1..^1].Replace("''", "'");
    }
    if (raw.Length >= 2 && raw[0] == '"' && raw[^1] == '"')
    {
      return raw[1..^1];
    }
    return ParseUnquotedScalar(raw);
  }

  private static object? ParseUnquotedScalar(string raw)
  {
    switch (raw)
    {
      case "true" or "True":
        return true;
      case "false" or "False":
        return false;
      case "null" or "~" or "Null":
        return null;
    }
    if (long.TryParse(raw, System.Globalization.NumberStyles.AllowLeadingSign, System.Globalization.CultureInfo.InvariantCulture, out var intValue)
        && !raw.StartsWith('+'))
    {
      return intValue;
    }
    if (double.TryParse(raw, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var doubleValue))
    {
      return doubleValue;
    }
    return raw;
  }

  private static List<object?> ParseFlowSequence(string inner)
  {
    var result = new List<object?>();
    inner = inner.Trim();
    if (inner.Length == 0)
    {
      return result;
    }
    foreach (var part in SplitFlowItems(inner))
    {
      result.Add(ParseScalar(part.Trim()));
    }
    return result;
  }

  // Only "{}" (an intentionally empty overlay document) is confirmed
  // present anywhere in config/ today, but a non-empty flow mapping is
  // handled too rather than left to throw on some future "{a: 1, b: 2}" —
  // cheap given SplitFlowItems' depth-aware splitting already exists for
  // ParseFlowSequence's sake.
  private static Dictionary<string, object?> ParseFlowMapping(string inner)
  {
    var result = new Dictionary<string, object?>();
    inner = inner.Trim();
    if (inner.Length == 0)
    {
      return result;
    }
    foreach (var part in SplitFlowItems(inner))
    {
      var entry = part.Trim();
      var colon = FindKeyColon(entry)
        ?? throw new FormatException($"MinimalYamlParser: expected 'key: value' inside flow mapping, got \"{entry}\"");
      var key = ParseKey(entry[..colon]);
      var value = ParseScalar(entry[(colon + 1)..]);
      result[key] = value;
    }
    return result;
  }

  private static IEnumerable<string> SplitFlowItems(string inner)
  {
    var depth = 0;
    var inSingle = false;
    var inDouble = false;
    var start = 0;
    for (var i = 0; i < inner.Length; i++)
    {
      var c = inner[i];
      if (c == '\'' && !inDouble)
      {
        inSingle = !inSingle;
      }
      else if (c == '"' && !inSingle)
      {
        inDouble = !inDouble;
      }
      else if (!inSingle && !inDouble)
      {
        if (c is '[' or '{')
        {
          depth++;
        }
        else if (c is ']' or '}')
        {
          depth--;
        }
        else if (c == ',' && depth == 0)
        {
          yield return inner[start..i];
          start = i + 1;
        }
      }
    }
    yield return inner[start..];
  }
}
