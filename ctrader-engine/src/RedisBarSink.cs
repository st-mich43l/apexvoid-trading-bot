using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using StackExchange.Redis;

namespace ApexVoid.CTraderFeed;

public interface IBarSink
{
  Task WriteClosedBarAsync(
    string symbol,
    string timeframe,
    OhlcBar bar,
    CancellationToken cancellationToken,
    bool publish = true
  );

  Task<long?> GetLatestTimestampAsync(
    string symbol,
    string timeframe,
    CancellationToken cancellationToken
  );

  Task<IReadOnlyList<OhlcBar>> ReadLatestAsync(
    string symbol,
    string timeframe,
    int count,
    CancellationToken cancellationToken
  );

  Task WriteSpotAsync(SpotPrice spot, CancellationToken cancellationToken);
}

public interface IRedisSeriesCommands
{
  Task RemoveByScoreAsync(string key, long score, CancellationToken cancellationToken);
  Task AddAsync(string key, string member, long score, CancellationToken cancellationToken);
  Task TrimToNewestAsync(string key, int keep, CancellationToken cancellationToken);
  Task PublishAsync(string channel, string payload, CancellationToken cancellationToken);
  Task<long?> LatestScoreAsync(string key, CancellationToken cancellationToken);
  Task<IReadOnlyList<RedisBarEntry>> ReadLatestAsync(
    string key,
    int count,
    CancellationToken cancellationToken
  );
}

public interface IAutoTradeStore
{
  // Dedicated cursor for the `manual_trade:commands` poll.
  Task<string> GetCommandCursorAsync(CancellationToken cancellationToken);
  Task SetCommandCursorAsync(string cursor, CancellationToken cancellationToken);
  // Dedicated cursor for the `execution:trade_plans` (TradePlan) stream.
  Task<string> GetTradePlanCursorAsync(CancellationToken cancellationToken) =>
    Task.FromResult("0-0");
  Task SetTradePlanCursorAsync(string cursor, CancellationToken cancellationToken) =>
    Task.CompletedTask;
  // Generic string get/set/delete - used by the TradePlan runtime for plan/position
  // state. Default no-ops keep minimal IAutoTradeStore fakes compiling;
  // TradePlanRuntimeTests uses a fake that overrides these for real in-memory
  // behavior.
  Task<string?> GetStringAsync(string key, CancellationToken cancellationToken) =>
    Task.FromResult<string?>(null);
  Task SetStringAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
  Task DeleteStringAsync(string key, CancellationToken cancellationToken) =>
    Task.CompletedTask;
  // Atomic SETNX-with-TTL claim - the only primitive the TradePlan runtime needs
  // for "at most one executor instance ever arms/submits this plan_id".
  Task<bool> TryClaimStringAsync(
    string key,
    string value,
    TimeSpan ttl,
    CancellationToken cancellationToken
  ) => Task.FromResult(false);
  Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
    string stream,
    string afterId,
    int count,
    CancellationToken cancellationToken
  );
  Task PublishAutoTradeEventAsync(
    string stream,
    AutoTradeEvent tradeEvent,
    CancellationToken cancellationToken
  );
  Task SetValueAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
  Task IncrementMetricAsync(
    string symbol,
    string metric,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
  // Recent closed bars for a symbol/timeframe, newest first - same shape
  // RedisBarSink.ReadLatestAsync already exposes, added here so the TradePlan
  // runtime (which only holds an IAutoTradeStore, not a RedisBarSink) can
  // check what price actually did across a gap it wasn't polling for
  // (see TradePlanRuntime's recovery catch-up). Default empty so every
  // existing IAutoTradeStore fake keeps compiling unchanged.
  Task<IReadOnlyList<OhlcBar>> ReadRecentBarsAsync(
    string symbol,
    string timeframe,
    int count,
    CancellationToken cancellationToken
  ) => Task.FromResult<IReadOnlyList<OhlcBar>>(Array.Empty<OhlcBar>());
}

public sealed class RedisBarSink(
  IRedisSeriesCommands redis,
  int windowMax,
  string channel,
  IRedisStringCommands? strings = null
) : IBarSink
{
  private readonly IRedisStringCommands? _strings = strings ?? redis as IRedisStringCommands;
  private readonly Dictionary<string, long> _lastSpotWriteMs = [];
  private const long SpotWriteMinIntervalMs = 250;

  public async Task WriteClosedBarAsync(
    string symbol,
    string timeframe,
    OhlcBar bar,
    CancellationToken cancellationToken,
    bool publish = true
  )
  {
    var key = Key(symbol, timeframe);
    var json = System.Text.Json.JsonSerializer.Serialize(
      RedisBar.From(bar),
      RedisJsonContext.Default.RedisBar
    );
    await redis.RemoveByScoreAsync(key, bar.Timestamp, cancellationToken);
    await redis.AddAsync(key, json, bar.Timestamp, cancellationToken);
    await redis.TrimToNewestAsync(key, windowMax, cancellationToken);
    if (publish)
    {
      await redis.PublishAsync(
        channel,
        $"{symbol.ToUpperInvariant()}:{timeframe.ToUpperInvariant()}:{bar.Timestamp}",
        cancellationToken
      );
    }
  }

  public Task<long?> GetLatestTimestampAsync(
    string symbol,
    string timeframe,
    CancellationToken cancellationToken
  ) => redis.LatestScoreAsync(Key(symbol, timeframe), cancellationToken);

  public async Task<IReadOnlyList<OhlcBar>> ReadLatestAsync(
    string symbol,
    string timeframe,
    int count,
    CancellationToken cancellationToken
  )
  {
    var entries = await redis.ReadLatestAsync(Key(symbol, timeframe), count, cancellationToken);
    return entries
      .Select(entry => System.Text.Json.JsonSerializer.Deserialize(
        entry.Json,
        RedisJsonContext.Default.RedisBar
      )!.ToOhlc())
      .ToArray();
  }

  public async Task WriteSpotAsync(SpotPrice spot, CancellationToken cancellationToken)
  {
    var strings = _strings
      ?? throw new InvalidOperationException("Redis string commands are required for spot writes");
    var key = SpotKey(spot.Symbol);
    // Wall-clock throttle (was 1s via spot.Timestamp) so Redis/spot consumers
    // see sub-second price updates for cards + activation.
    var nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    if (
      _lastSpotWriteMs.TryGetValue(key, out var lastMs)
      && nowMs - lastMs < SpotWriteMinIntervalMs
    )
    {
      return;
    }
    var json = System.Text.Json.JsonSerializer.Serialize(
      RedisSpot.From(spot),
      RedisJsonContext.Default.RedisSpot
    );
    await strings.SetStringAsync(key, json, cancellationToken);
    _lastSpotWriteMs[key] = nowMs;
    await redis.PublishAsync(
      "spots:new",
      $"{spot.Symbol.ToUpperInvariant()}:{spot.Timestamp}",
      cancellationToken
    );
  }

  public static string Key(string symbol, string timeframe) =>
    $"bars:{symbol.ToUpperInvariant()}:{timeframe.ToUpperInvariant()}";

  public static string SpotKey(string symbol) =>
    $"price:{symbol.ToUpperInvariant()}:spot";
}

public sealed class StackExchangeRedisSeriesCommands :
  IRedisSeriesCommands,
  IRedisStringCommands,
  IAutoTradeStore,
  IAsyncDisposable
{
  private readonly IConnectionMultiplexer _connection;
  private readonly IDatabase _db;
  private readonly ISubscriber _subscriber;
  private StackExchangeRedisSeriesCommands(IConnectionMultiplexer connection)
  {
    _connection = connection;
    _db = connection.GetDatabase();
    _subscriber = connection.GetSubscriber();
  }

  public static async Task<StackExchangeRedisSeriesCommands> ConnectAsync(string redisUrl)
  {
    var options = ParseRedisUrl(redisUrl);
    var connection = await ConnectionMultiplexer.ConnectAsync(options);
    return new StackExchangeRedisSeriesCommands(connection);
  }

  public Task RemoveByScoreAsync(
    string key,
    long score,
    CancellationToken cancellationToken
  ) => _db.SortedSetRemoveRangeByScoreAsync(key, score, score);

  public Task AddAsync(
    string key,
    string member,
    long score,
    CancellationToken cancellationToken
  ) => _db.SortedSetAddAsync(key, member, score);

  public Task TrimToNewestAsync(
    string key,
    int keep,
    CancellationToken cancellationToken
  ) => _db.SortedSetRemoveRangeByRankAsync(key, 0, -(keep + 1));

  public Task PublishAsync(
    string channel,
    string payload,
    CancellationToken cancellationToken
  ) => _subscriber.PublishAsync(RedisChannel.Literal(channel), payload);

  public async Task<long?> LatestScoreAsync(string key, CancellationToken cancellationToken)
  {
    var entries = await _db.SortedSetRangeByRankWithScoresAsync(
      key,
      -1,
      -1,
      Order.Ascending
    );
    return entries.Length == 0
      ? null
      : Convert.ToInt64(entries[0].Score, CultureInfo.InvariantCulture);
  }

  public async Task<IReadOnlyList<RedisBarEntry>> ReadLatestAsync(
    string key,
    int count,
    CancellationToken cancellationToken
  )
  {
    var entries = await _db.SortedSetRangeByRankWithScoresAsync(
      key,
      0,
      count - 1,
      Order.Descending
    );
    return entries
      .Select(entry => new RedisBarEntry(
        Convert.ToInt64(entry.Score, CultureInfo.InvariantCulture),
        entry.Element.ToString()
      ))
      .ToArray();
  }

  public async Task<IReadOnlyList<OhlcBar>> ReadRecentBarsAsync(
    string symbol,
    string timeframe,
    int count,
    CancellationToken cancellationToken
  )
  {
    // Mirrors RedisBarSink.ReadLatestAsync's own key/deserialize logic
    // exactly - that method lives on RedisBarSink, not on this store, and
    // the TradePlan runtime only holds an IAutoTradeStore.
    var entries = await ReadLatestAsync(
      RedisBarSink.Key(symbol, timeframe), count, cancellationToken
    );
    return entries
      .Select(entry => System.Text.Json.JsonSerializer.Deserialize(
        entry.Json,
        RedisJsonContext.Default.RedisBar
      )!.ToOhlc())
      .ToArray();
  }

  public async Task<string?> GetStringAsync(
    string key,
    CancellationToken cancellationToken
  )
  {
    var value = await _db.StringGetAsync(key);
    return value.HasValue ? value.ToString() : null;
  }

  public Task SetStringAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  ) => _db.StringSetAsync(key, value);

  public Task DeleteStringAsync(
    string key,
    CancellationToken cancellationToken
  ) => _db.KeyDeleteAsync(key);

  public async Task<bool> TryClaimStringAsync(
    string key,
    string value,
    TimeSpan ttl,
    CancellationToken cancellationToken
  ) => await _db.StringSetAsync(key, value, ttl, When.NotExists);

  public async Task<string> GetTradePlanCursorAsync(CancellationToken cancellationToken)
  {
    var value = await _db.StringGetAsync("execution:trade_plan_cursor");
    return value.HasValue ? value.ToString() : "0-0";
  }

  public Task SetTradePlanCursorAsync(string cursor, CancellationToken cancellationToken) =>
    _db.StringSetAsync("execution:trade_plan_cursor", cursor);

  public async Task<string> GetCommandCursorAsync(CancellationToken cancellationToken)
  {
    var value = await _db.StringGetAsync("manual_trade:command_cursor");
    return value.HasValue ? value.ToString() : "0-0";
  }

  public Task SetCommandCursorAsync(string cursor, CancellationToken cancellationToken) =>
    _db.StringSetAsync("manual_trade:command_cursor", cursor);

  public async Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
    string stream,
    string afterId,
    int count,
    CancellationToken cancellationToken
  )
  {
    var entries = await _db.StreamReadAsync(stream, afterId, count);
    return entries.Select(entry => new TradeStreamEntry(
      entry.Id.ToString(),
      entry.Values.FirstOrDefault(pair => pair.Name == "payload").Value.ToString()
    )).Where(entry => !string.IsNullOrWhiteSpace(entry.Payload)).ToArray();
  }

  public Task PublishAutoTradeEventAsync(
    string stream,
    AutoTradeEvent tradeEvent,
    CancellationToken cancellationToken
  ) => _db.StreamAddAsync(
    stream,
    [new NameValueEntry(
      "payload",
      System.Text.Json.JsonSerializer.Serialize(
        tradeEvent,
        RedisJsonContext.Default.AutoTradeEvent
      )
    )],
    maxLength: 1000,
    useApproximateMaxLength: true
  );

  public Task SetValueAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  ) => _db.StringSetAsync(key, value);

  public Task IncrementMetricAsync(
    string symbol,
    string metric,
    CancellationToken cancellationToken
  ) => _db.HashIncrementAsync(
    $"auto_trade:metrics:{symbol.ToUpperInvariant()}",
    metric,
    1
  );

  private async Task RecordEvaluationDimensionsAsync(
    AutoTradeEvent tradeEvent
  )
  {
    var prefix = $"auto_trade:evaluation:{tradeEvent.Symbol.ToUpperInvariant()}";
    var state = tradeEvent.State ?? tradeEvent.Type;
    var timestamp = DateTimeOffset.FromUnixTimeSeconds(tradeEvent.Timestamp);
    var hour = timestamp.UtcDateTime.ToString("HH", CultureInfo.InvariantCulture);
    var session = timestamp.Hour switch
    {
      < 7 => "asia",
      < 13 => "london",
      < 21 => "new_york",
      _ => "rollover",
    };
    var dimensions = new List<(string Name, string? Value)>
    {
      ("strategy", tradeEvent.Setup),
      ("strategy_family", tradeEvent.StrategyFamily),
      ("direction", tradeEvent.Direction),
      ("range_side", tradeEvent.RangeId is null ? null : tradeEvent.Direction),
      ("detector", tradeEvent.MatchId is null ? null : tradeEvent.Setup),
      ("execution_route", tradeEvent.Type),
      ("rejection_reason", tradeEvent.ReasonCode),
      ("structural_source", tradeEvent.StructuralSource),
      ("structural_zone_id", tradeEvent.StructuralZoneId),
      ("reaction_id", tradeEvent.ReactionId),
      ("thesis_id", tradeEvent.ThesisId),
      ("hour_utc", hour),
      ("session_utc", session),
    };
    foreach (var (name, value) in dimensions)
    {
      if (!string.IsNullOrWhiteSpace(value))
      {
        await _db.HashIncrementAsync(
          $"{prefix}:{name}",
          $"{state}:{value}",
          1
        );
      }
    }
  }

  public async ValueTask DisposeAsync()
  {
    await _connection.CloseAsync();
    await _connection.DisposeAsync();
  }

  private static ConfigurationOptions ParseRedisUrl(string redisUrl)
  {
    var uri = new Uri(redisUrl);
    var options = new ConfigurationOptions
    {
      AbortOnConnectFail = false,
      Ssl = uri.Scheme.Equals("rediss", StringComparison.OrdinalIgnoreCase),
    };
    options.EndPoints.Add(uri.Host, uri.Port);
    var dbText = uri.AbsolutePath.Trim('/');
    if (int.TryParse(dbText, out var database))
    {
      options.DefaultDatabase = database;
    }
    if (!string.IsNullOrWhiteSpace(uri.UserInfo))
    {
      var parts = uri.UserInfo.Split(':', 2);
      if (parts.Length == 2)
      {
        options.User = Uri.UnescapeDataString(parts[0]);
        options.Password = Uri.UnescapeDataString(parts[1]);
      }
      else
      {
        options.Password = Uri.UnescapeDataString(parts[0]);
      }
    }
    return options;
  }

}

internal sealed record RedisBar(
  [property: JsonPropertyName("t")] long T,
  [property: JsonPropertyName("o")] decimal O,
  [property: JsonPropertyName("h")] decimal H,
  [property: JsonPropertyName("l")] decimal L,
  [property: JsonPropertyName("c")] decimal C,
  [property: JsonPropertyName("v")] long V
)
{
  public static RedisBar From(OhlcBar bar) =>
    new(bar.Timestamp, bar.Open, bar.High, bar.Low, bar.Close, bar.Volume);

  public OhlcBar ToOhlc() => new(T, O, H, L, C, V);
}

internal sealed record RedisSpot(
  [property: JsonPropertyName("bid")] decimal Bid,
  [property: JsonPropertyName("ask")] decimal Ask,
  [property: JsonPropertyName("ts")] long Ts
)
{
  public static RedisSpot From(SpotPrice spot) => new(spot.Bid, spot.Ask, spot.Timestamp);
}

[JsonSourceGenerationOptions(
  DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
  PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower
)]
[JsonSerializable(typeof(RedisBar))]
[JsonSerializable(typeof(RedisSpot))]
[JsonSerializable(typeof(AutoTradeEvent))]
[JsonSerializable(typeof(ManualTradeCommand))]
[JsonSerializable(typeof(RefreshTokenDocument))]
[JsonSerializable(typeof(AutoTradeExecutorReadiness))]
[JsonSerializable(typeof(AutoTradeExecutorSnapshot))]
[JsonSerializable(typeof(TradePlan))]
internal sealed partial class RedisJsonContext : JsonSerializerContext
{
}
