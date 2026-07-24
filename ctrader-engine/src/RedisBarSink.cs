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
  Task<string> GetCursorAsync(CancellationToken cancellationToken);
  Task SetCursorAsync(string cursor, CancellationToken cancellationToken);
  // Dedicated cursor for the `manual_trade:commands` poll - separate key so
  // it never collides with the candidate-stream cursor above.
  Task<string> GetCommandCursorAsync(CancellationToken cancellationToken);
  Task SetCommandCursorAsync(string cursor, CancellationToken cancellationToken);
  Task<IReadOnlyList<TradeStreamEntry>> ReadCandidatesAsync(
    string stream,
    string afterId,
    int count,
    CancellationToken cancellationToken
  );
  Task<bool> TryClaimCandidateAsync(
    string candidateId,
    CancellationToken cancellationToken
  );
  Task<string?> GetCandidateStatusAsync(
    string candidateId,
    CancellationToken cancellationToken
  );
  Task CompleteCandidateAsync(
    string candidateId,
    string outcome,
    CancellationToken cancellationToken
  );
  Task ReleaseCandidateAsync(string candidateId, CancellationToken cancellationToken);
  Task SavePositionAsync(
    AutoTradePositionState state,
    CancellationToken cancellationToken
  );
  Task<AutoTradePositionState?> GetPositionAsync(
    long positionId,
    CancellationToken cancellationToken
  );
  Task<IReadOnlyList<long>> GetTrackedPositionIdsAsync(
    CancellationToken cancellationToken
  );
  Task DeletePositionAsync(long positionId, CancellationToken cancellationToken);
  Task<long> GetDailyTradeCountAsync(
    DateOnly date,
    CancellationToken cancellationToken
  );
  Task<long> IncrementDailyTradeCountAsync(
    DateOnly date,
    CancellationToken cancellationToken
  );
  Task<bool> IsPausedAsync(CancellationToken cancellationToken);
  Task PublishAutoTradeEventAsync(
    string stream,
    AutoTradeEvent tradeEvent,
    CancellationToken cancellationToken
  );
  Task IncrementGateRejectAsync(
    string symbol,
    string condition,
    CancellationToken cancellationToken
  );
  Task IncrementAddRejectAsync(
    string symbol,
    string mode,
    string condition,
    CancellationToken cancellationToken
  );
  Task RecordZoneCooldownAsync(
    string symbol,
    string direction,
    ZoneCooldownRecord record,
    int ttlMinutes,
    CancellationToken cancellationToken
  );
  Task SetValueAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
  Task<string?> GetValueAsync(
    string key,
    CancellationToken cancellationToken
  ) => Task.FromResult<string?>(null);
  Task IncrementMetricAsync(
    string symbol,
    string metric,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
  Task RecordLifecycleEventAsync(
    AutoTradeEvent tradeEvent,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
  Task UpdateRangeSideStateAsync(
    string symbol,
    string rangeId,
    string direction,
    string state,
    string? candidateId,
    long? positionId,
    IReadOnlyList<long>? pendingOrderIds,
    CancellationToken cancellationToken
  ) => Task.CompletedTask;
}

public sealed class RedisBarSink(
  IRedisSeriesCommands redis,
  int windowMax,
  string channel,
  IRedisStringCommands? strings = null
) : IBarSink
{
  private readonly IRedisStringCommands? _strings = strings ?? redis as IRedisStringCommands;
  private readonly Dictionary<string, long> _lastSpotWrite = [];

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
    if (
      _lastSpotWrite.TryGetValue(key, out var last)
      && spot.Timestamp - last < 1
    )
    {
      return;
    }
    var json = System.Text.Json.JsonSerializer.Serialize(
      RedisSpot.From(spot),
      RedisJsonContext.Default.RedisSpot
    );
    await strings.SetStringAsync(key, json, cancellationToken);
    _lastSpotWrite[key] = spot.Timestamp;
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

  public async Task<string> GetCursorAsync(CancellationToken cancellationToken)
  {
    var value = await _db.StringGetAsync("auto_trade:cursor");
    return value.HasValue ? value.ToString() : "0-0";
  }

  public Task SetCursorAsync(string cursor, CancellationToken cancellationToken) =>
    _db.StringSetAsync("auto_trade:cursor", cursor);

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

  public Task<bool> TryClaimCandidateAsync(
    string candidateId,
    CancellationToken cancellationToken
  ) => _db.StringSetAsync(
    CandidateKey(candidateId),
    "processing",
    TimeSpan.FromSeconds(30),
    When.NotExists
  );

  public async Task<string?> GetCandidateStatusAsync(
    string candidateId,
    CancellationToken cancellationToken
  )
  {
    var value = await _db.StringGetAsync(CandidateKey(candidateId));
    return value.HasValue ? value.ToString() : null;
  }

  public Task CompleteCandidateAsync(
    string candidateId,
    string outcome,
    CancellationToken cancellationToken
  ) => _db.StringSetAsync(
    CandidateKey(candidateId),
    outcome,
    TimeSpan.FromDays(7)
  );

  public Task ReleaseCandidateAsync(
    string candidateId,
    CancellationToken cancellationToken
  ) => _db.KeyDeleteAsync(CandidateKey(candidateId));

  public async Task SavePositionAsync(
    AutoTradePositionState state,
    CancellationToken cancellationToken
  )
  {
    await _db.StringSetAsync(
      PositionKey(state.PositionId),
      System.Text.Json.JsonSerializer.Serialize(
        state,
        RedisJsonContext.Default.AutoTradePositionState
      )
    );
    await _db.SetAddAsync(TrackedPositionsKey, state.PositionId);
  }

  public async Task<AutoTradePositionState?> GetPositionAsync(
    long positionId,
    CancellationToken cancellationToken
  )
  {
    var value = await _db.StringGetAsync(PositionKey(positionId));
    return value.HasValue
      ? System.Text.Json.JsonSerializer.Deserialize(
        value.ToString(),
        RedisJsonContext.Default.AutoTradePositionState
      )
      : null;
  }

  public async Task<IReadOnlyList<long>> GetTrackedPositionIdsAsync(
    CancellationToken cancellationToken
  )
  {
    var members = await _db.SetMembersAsync(TrackedPositionsKey);
    return members
      .Select(member => long.TryParse(member.ToString(), out var id) ? id : 0)
      .Where(id => id > 0)
      .ToArray();
  }

  public async Task DeletePositionAsync(
    long positionId,
    CancellationToken cancellationToken
  )
  {
    await _db.KeyDeleteAsync(PositionKey(positionId));
    await _db.SetRemoveAsync(TrackedPositionsKey, positionId);
  }

  public async Task<long> GetDailyTradeCountAsync(
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    var value = await _db.StringGetAsync(DailyKey(date));
    return value.HasValue ? (long)value : 0;
  }

  public async Task<long> IncrementDailyTradeCountAsync(
    DateOnly date,
    CancellationToken cancellationToken
  )
  {
    var key = DailyKey(date);
    var value = await _db.StringIncrementAsync(key);
    await _db.KeyExpireAsync(key, TimeSpan.FromDays(3));
    return value;
  }

  public async Task<bool> IsPausedAsync(CancellationToken cancellationToken)
  {
    var value = await _db.StringGetAsync("auto_trade:paused");
    return value.HasValue && value == "1";
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

  public Task IncrementGateRejectAsync(
    string symbol,
    string condition,
    CancellationToken cancellationToken
  ) => _db.HashIncrementAsync(
    $"auto_trade:gate_reject:{symbol.ToUpperInvariant()}:{condition}",
    "count",
    1
  );

  public Task IncrementAddRejectAsync(
    string symbol,
    string mode,
    string condition,
    CancellationToken cancellationToken
  ) => _db.HashIncrementAsync(
    $"auto_trade:add_reject:{symbol.ToUpperInvariant()}:{mode}:{condition}",
    "count",
    1
  );

  public Task RecordZoneCooldownAsync(
    string symbol,
    string direction,
    ZoneCooldownRecord record,
    int ttlMinutes,
    CancellationToken cancellationToken
  ) => _db.StringSetAsync(
    $"auto_trade:zone:cooldown:{symbol.ToUpperInvariant()}:{direction.ToUpperInvariant()}",
    System.Text.Json.JsonSerializer.Serialize(
      record,
      RedisJsonContext.Default.ZoneCooldownRecord
    ),
    TimeSpan.FromMinutes(Math.Max(1, ttlMinutes))
  );

  public Task SetValueAsync(
    string key,
    string value,
    CancellationToken cancellationToken
  ) => _db.StringSetAsync(key, value);

  public async Task<string?> GetValueAsync(
    string key,
    CancellationToken cancellationToken
  )
  {
    var value = await _db.StringGetAsync(key);
    return value.HasValue ? value.ToString() : null;
  }

  public Task IncrementMetricAsync(
    string symbol,
    string metric,
    CancellationToken cancellationToken
  ) => _db.HashIncrementAsync(
    $"auto_trade:metrics:{symbol.ToUpperInvariant()}",
    metric,
    1
  );

  public async Task RecordLifecycleEventAsync(
    AutoTradeEvent tradeEvent,
    CancellationToken cancellationToken
  )
  {
    var owner = tradeEvent.CandidateId
      ?? tradeEvent.GroupId
      ?? tradeEvent.CorrelationId
      ?? "service";
    var payload = System.Text.Json.JsonSerializer.Serialize(
      tradeEvent,
      RedisJsonContext.Default.AutoTradeEvent
    );
    var historyKey = $"auto_trade:lifecycle:{owner}";
    await _db.ListRightPushAsync(historyKey, payload);
    await _db.ListTrimAsync(historyKey, -100, -1);
    await _db.KeyExpireAsync(historyKey, TimeSpan.FromDays(7));
    await _db.StringSetAsync(
      $"auto_trade:lifecycle_state:{owner}",
      tradeEvent.State ?? "managing",
      TimeSpan.FromDays(7)
    );
    await _db.StringSetAsync(
      $"auto_trade:last_lifecycle:{tradeEvent.Symbol.ToUpperInvariant()}",
      payload
    );
    await _db.StringSetAsync(
      $"auto_trade:last_executor_decision:{tradeEvent.Symbol.ToUpperInvariant()}",
      payload
    );
    await _db.StreamAddAsync(
      "auto_trade:lifecycle_events",
      [new NameValueEntry("payload", payload)],
      maxLength: 5000,
      useApproximateMaxLength: true
    );
    await RecordEvaluationDimensionsAsync(tradeEvent);
  }

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

  public async Task UpdateRangeSideStateAsync(
    string symbol,
    string rangeId,
    string direction,
    string state,
    string? candidateId,
    long? positionId,
    IReadOnlyList<long>? pendingOrderIds,
    CancellationToken cancellationToken
  )
  {
    var key = $"auto_trade:range_side:{symbol.ToUpperInvariant()}:{rangeId}:"
      + direction.ToUpperInvariant();
    var existing = await _db.StringGetAsync(key);
    JsonObject payload;
    try
    {
      payload = existing.HasValue
        ? JsonNode.Parse(existing.ToString()) as JsonObject ?? new JsonObject()
        : new JsonObject();
    }
    catch (System.Text.Json.JsonException)
    {
      payload = new JsonObject();
    }
    payload["symbol"] = symbol.ToUpperInvariant();
    payload["range_id"] = rangeId;
    payload["direction"] = direction.ToUpperInvariant();
    payload["candidate_id"] = candidateId;
    if (pendingOrderIds is not null)
    {
      payload["pending_order_ids"] = new JsonArray(
        pendingOrderIds.Select(
          value => (JsonNode?)JsonValue.Create(value)
        ).ToArray()
      );
    }
    var positions = payload["position_ids"] as JsonArray ?? new JsonArray();
    if (positionId is long id)
    {
      var existingIds = positions
        .Select(item => item?.GetValue<long>())
        .Where(item => item is not null)
        .Select(item => item!.Value)
        .ToHashSet();
      if (state.Equals("CLOSED", StringComparison.OrdinalIgnoreCase))
      {
        positions = new JsonArray(
          existingIds.Where(value => value != id)
            .Select(value => (JsonNode?)JsonValue.Create(value))
            .ToArray()
        );
      }
      else if (existingIds.Add(id))
      {
        positions.Add((JsonNode?)JsonValue.Create(id));
      }
    }
    payload["position_ids"] = positions;
    payload["state"] = (
      state.Equals("CLOSED", StringComparison.OrdinalIgnoreCase)
      && positions.Count > 0
    ) ? "MANAGING" : state.ToUpperInvariant();
    payload["updated_at"] = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    await _db.StringSetAsync(
      key,
      payload.ToJsonString(),
      TimeSpan.FromHours(4)
    );
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

  private static string CandidateKey(string candidateId) =>
    $"auto_trade:executor:candidate:{candidateId}";

  private static string PositionKey(long positionId) =>
    $"auto_trade:position:{positionId}";

  private const string TrackedPositionsKey = "auto_trade:positions";

  private static string DailyKey(DateOnly date) =>
    $"auto_trade:daily:{date:yyyyMMdd}:trades";
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
[JsonSerializable(typeof(TradeCandidate))]
[JsonSerializable(typeof(AutoTradePositionState))]
[JsonSerializable(typeof(AutoTradeEvent))]
[JsonSerializable(typeof(ManualTradeCommand))]
[JsonSerializable(typeof(ZoneCooldownRecord))]
[JsonSerializable(typeof(RefreshTokenDocument))]
[JsonSerializable(typeof(AutoTradeConfigManifest))]
[JsonSerializable(typeof(AutoTradeConfigHealthDocument))]
[JsonSerializable(typeof(AutoTradeExecutorReadiness))]
[JsonSerializable(typeof(AutoTradeExecutorSnapshot))]
[JsonSerializable(typeof(AutoTradeGroupPlan))]
internal sealed partial class RedisJsonContext : JsonSerializerContext
{
}
