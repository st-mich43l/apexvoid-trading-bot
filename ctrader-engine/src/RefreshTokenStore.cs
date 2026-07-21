using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using OpenAPI.Net;

namespace ApexVoid.CTraderFeed;

public interface IRefreshTokenStore
{
  Task<string?> GetAsync(CancellationToken cancellationToken);
  Task SetAsync(string token, CancellationToken cancellationToken);
  Task DeleteAsync(CancellationToken cancellationToken);
}

public interface IRedisStringCommands
{
  Task<string?> GetStringAsync(string key, CancellationToken cancellationToken);
  Task SetStringAsync(string key, string value, CancellationToken cancellationToken);
  Task DeleteStringAsync(string key, CancellationToken cancellationToken);
}

public sealed class RedisRefreshTokenStore(
  IRedisStringCommands redis,
  string key
) : IRefreshTokenStore
{
  public Task<string?> GetAsync(CancellationToken cancellationToken) =>
    redis.GetStringAsync(key, cancellationToken);

  public Task SetAsync(string token, CancellationToken cancellationToken) =>
    redis.SetStringAsync(key, token, cancellationToken);

  public Task DeleteAsync(CancellationToken cancellationToken) =>
    redis.DeleteStringAsync(key, cancellationToken);
}

internal sealed record RefreshTokenDocument(string Seed, string Current);

public sealed class RefreshTokenState(
  FeedOptions options,
  IRefreshTokenStore store,
  Action<string>? log = null
)
{
  private readonly string _seed = Fingerprint(options.RefreshToken);
  private readonly Action<string> _log = log ?? Log;
  public string AccessToken { get; private set; } = options.AccessToken;
  public string RefreshToken { get; private set; } = options.RefreshToken;

  public async Task SeedAsync(CancellationToken cancellationToken)
  {
    var persisted = await store.GetAsync(cancellationToken);
    if (TryReadDocument(persisted, out var document))
    {
      if (string.Equals(document.Seed, _seed, StringComparison.OrdinalIgnoreCase))
      {
        RefreshToken = document.Current;
        return;
      }
      LogSeedChange(document.Seed);
    }
    else if (!string.IsNullOrWhiteSpace(persisted))
    {
      LogSeedChange(Fingerprint(persisted));
    }
    RefreshToken = options.RefreshToken;
    await PersistAsync(cancellationToken);
  }

  public async Task ApplyAsync(
    ProtoOARefreshTokenRes response,
    CancellationToken cancellationToken
  )
  {
    if (!string.IsNullOrWhiteSpace(response.AccessToken))
    {
      AccessToken = response.AccessToken;
    }
    if (
      !string.IsNullOrWhiteSpace(response.RefreshToken)
      && response.RefreshToken != RefreshToken
    )
    {
      RefreshToken = response.RefreshToken;
      await PersistAsync(cancellationToken);
    }
  }

  private Task PersistAsync(CancellationToken cancellationToken) =>
    store.SetAsync(
      JsonSerializer.Serialize(
        new RefreshTokenDocument(_seed, RefreshToken),
        RedisJsonContext.Default.RefreshTokenDocument
      ),
      cancellationToken
    );

  private void LogSeedChange(string oldSeed) =>
    _log(
      "refresh token in .env changed "
      + $"(seed {Short(oldSeed)}... -> {Short(_seed)}...) -- "
      + "discarding cached rotation chain"
    );

  private static bool TryReadDocument(
    string? value,
    out RefreshTokenDocument document
  )
  {
    document = null!;
    if (string.IsNullOrWhiteSpace(value))
    {
      return false;
    }
    try
    {
      var parsed = JsonSerializer.Deserialize(
        value,
        RedisJsonContext.Default.RefreshTokenDocument
      );
      if (
        parsed is null
        || string.IsNullOrWhiteSpace(parsed.Seed)
        || parsed.Seed.Length != 64
        || parsed.Seed.Any(value => !Uri.IsHexDigit(value))
        || string.IsNullOrWhiteSpace(parsed.Current)
      )
      {
        return false;
      }
      document = parsed;
      return true;
    }
    catch (JsonException)
    {
      return false;
    }
  }

  internal static string Fingerprint(string value) =>
    Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))
      .ToLowerInvariant();

  private static string Short(string fingerprint) =>
    fingerprint[..Math.Min(8, fingerprint.Length)];

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed INFO {message}");
}
