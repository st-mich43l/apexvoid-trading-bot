using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using OpenAPI.Net;

namespace ApexVoid.CTraderFeed;

public interface IRefreshTokenStore
{
  string LastReadTier => "unknown";

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
  public string LastReadTier => "redis";

  public Task<string?> GetAsync(CancellationToken cancellationToken) =>
    redis.GetStringAsync(key, cancellationToken);

  public Task SetAsync(string token, CancellationToken cancellationToken) =>
    redis.SetStringAsync(key, token, cancellationToken);

  public Task DeleteAsync(CancellationToken cancellationToken) =>
    redis.DeleteStringAsync(key, cancellationToken);
}

public sealed class FileRefreshTokenStore(
  string path,
  Action<string>? warningLog = null
) : IRefreshTokenStore
{
  private static readonly UnixFileMode OwnerReadWrite =
    UnixFileMode.UserRead | UnixFileMode.UserWrite;

  public string LastReadTier => "file";

  public async Task<string?> GetAsync(CancellationToken cancellationToken)
  {
    try
    {
      return File.Exists(path)
        ? await File.ReadAllTextAsync(path, cancellationToken)
        : null;
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token mirror read failed: {exception.GetType().Name}: {exception.Message}");
      return null;
    }
  }

  public async Task SetAsync(string token, CancellationToken cancellationToken)
  {
    string? temporaryPath = null;
    try
    {
      var directory = Path.GetDirectoryName(path);
      if (string.IsNullOrWhiteSpace(directory))
      {
        directory = ".";
      }
      Directory.CreateDirectory(directory);
      temporaryPath = Path.Combine(
        directory,
        $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp"
      );
      var bytes = Encoding.UTF8.GetBytes(token);
      await using (
        var stream = new FileStream(
          temporaryPath,
          FileMode.CreateNew,
          FileAccess.Write,
          FileShare.None,
          4096,
          FileOptions.Asynchronous | FileOptions.WriteThrough
        )
      )
      {
        await stream.WriteAsync(bytes, cancellationToken);
        await stream.FlushAsync(cancellationToken);
        stream.Flush(flushToDisk: true);
      }
      SetOwnerOnlyMode(temporaryPath);
      File.Move(temporaryPath, path, overwrite: true);
      temporaryPath = null;
      SetOwnerOnlyMode(path);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token mirror write failed: {exception.GetType().Name}: {exception.Message}");
    }
    finally
    {
      if (temporaryPath is not null)
      {
        try
        {
          File.Delete(temporaryPath);
        }
        catch (Exception exception)
        {
          Warn(
            $"token mirror temp cleanup failed: {exception.GetType().Name}: {exception.Message}"
          );
        }
      }
    }
  }

  public Task DeleteAsync(CancellationToken cancellationToken)
  {
    try
    {
      cancellationToken.ThrowIfCancellationRequested();
      File.Delete(path);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token mirror delete failed: {exception.GetType().Name}: {exception.Message}");
    }
    return Task.CompletedTask;
  }

  private void Warn(string message) => (warningLog ?? Log)(message);

  private static void SetOwnerOnlyMode(string filePath)
  {
    if (!OperatingSystem.IsWindows())
    {
      File.SetUnixFileMode(filePath, OwnerReadWrite);
    }
  }

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed WARNING {message}");
}

public sealed class TieredRefreshTokenStore(
  IRefreshTokenStore primary,
  IRefreshTokenStore mirror,
  Func<string, string, CancellationToken, Task>? notify = null,
  Action<string>? warningLog = null,
  TokenEventNotifier? notifications = null
) : IRefreshTokenStore
{
  private readonly TokenEventNotifier _notifications = notifications
    ?? new TokenEventNotifier(notify, warningLog: warningLog);
  private string _lastReadTier = "none";

  public string LastReadTier => _lastReadTier;

  public async Task<string?> GetAsync(CancellationToken cancellationToken)
  {
    string? primaryValue = null;
    try
    {
      primaryValue = await primary.GetAsync(cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token store primary read failed: {exception.GetType().Name}: {exception.Message}");
    }
    if (RefreshTokenState.TryReadDocument(primaryValue, out _))
    {
      _lastReadTier = primary.LastReadTier;
      return primaryValue;
    }

    string? mirrorValue;
    try
    {
      mirrorValue = await mirror.GetAsync(cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token store mirror read failed: {exception.GetType().Name}: {exception.Message}");
      mirrorValue = null;
    }
    if (!RefreshTokenState.TryReadDocument(mirrorValue, out _))
    {
      _lastReadTier = "none";
      return null;
    }

    _lastReadTier = mirror.LastReadTier;
    const string warning =
      "token store: primary empty, recovered from mirror -- Redis volume may have been lost";
    Warn(warning);
    await _notifications.NotifyAsync("mirror-recovery", warning, cancellationToken);
    try
    {
      await primary.SetAsync(mirrorValue!, cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token store primary heal failed: {exception.GetType().Name}: {exception.Message}");
    }
    return mirrorValue;
  }

  public async Task SetAsync(string token, CancellationToken cancellationToken)
  {
    try
    {
      await mirror.SetAsync(token, cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token store mirror write failed: {exception.GetType().Name}: {exception.Message}");
    }
    await primary.SetAsync(token, cancellationToken);
  }

  public async Task DeleteAsync(CancellationToken cancellationToken)
  {
    try
    {
      await mirror.DeleteAsync(cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      Warn($"token store mirror delete failed: {exception.GetType().Name}: {exception.Message}");
    }
    await primary.DeleteAsync(cancellationToken);
  }

  private void Warn(string message) => (warningLog ?? Log)(message);

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed WARNING {message}");
}

internal sealed record RefreshTokenDocument(
  string Seed,
  string Current,
  string? AccessToken = null,
  long ExpiresAtUnixSeconds = 0
);

public sealed class RefreshTokenState(
  FeedOptions options,
  IRefreshTokenStore store,
  Action<string>? log = null,
  Func<string, string, CancellationToken, Task>? notify = null,
  TokenEventNotifier? notifications = null
)
{
  private readonly string _seed = Fingerprint(options.RefreshToken);
  private readonly Action<string> _log = log ?? Log;
  private readonly TokenEventNotifier _notifications = notifications
    ?? new TokenEventNotifier(notify);
  private string _tier = "environment";
  public string AccessToken { get; private set; } = options.AccessToken;
  public string RefreshToken { get; private set; } = options.RefreshToken;
  public DateTimeOffset? ExpiresAt { get; private set; }
  public string SeedFingerprint => _seed;
  public TokenLifecycleStatus Status => new(_tier, ExpiresAt, _seed);

  public async Task SeedAsync(CancellationToken cancellationToken)
  {
    var persisted = await store.GetAsync(cancellationToken);
    if (TryReadDocument(persisted, out var document))
    {
      if (string.Equals(document.Seed, _seed, StringComparison.OrdinalIgnoreCase))
      {
        RefreshToken = document.Current;
        if (!string.IsNullOrWhiteSpace(document.AccessToken))
        {
          AccessToken = document.AccessToken;
        }
        ExpiresAt = ReadExpiry(document.ExpiresAtUnixSeconds);
        _tier = store.LastReadTier;
        return;
      }
      await LogSeedChangeAsync(document.Seed, cancellationToken);
    }
    else if (!string.IsNullOrWhiteSpace(persisted))
    {
      await LogSeedChangeAsync(Fingerprint(persisted), cancellationToken);
    }
    _tier = "environment";
    AccessToken = options.AccessToken;
    RefreshToken = options.RefreshToken;
    ExpiresAt = null;
    await PersistAsync(cancellationToken);
  }

  internal async Task ApplyAsync(
    ProtoOARefreshTokenRes response,
    TokenExpiryResolution expiry,
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
    }
    ExpiresAt = expiry.ExpiresAt;
    await PersistAsync(cancellationToken);
  }

  private Task PersistAsync(CancellationToken cancellationToken) =>
    store.SetAsync(
      JsonSerializer.Serialize(
        new RefreshTokenDocument(
          _seed,
          RefreshToken,
          AccessToken,
          ExpiresAt?.ToUnixTimeSeconds() ?? 0
        ),
        RedisJsonContext.Default.RefreshTokenDocument
      ),
      cancellationToken
    );

  private async Task LogSeedChangeAsync(
    string oldSeed,
    CancellationToken cancellationToken
  )
  {
    _log(
      "refresh token in .env changed "
      + $"(seed {Short(oldSeed)}... -> {Short(_seed)}...) -- "
      + "discarding cached rotation chain"
    );
    await _notifications.NotifyAsync(
      "seed-changed",
      "cTrader token seed changed: "
      + $"old={Short(oldSeed)} new={Short(_seed)}; discarded cached rotation chain",
      cancellationToken
    );
  }

  internal static bool TryReadDocument(
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

  private static DateTimeOffset? ReadExpiry(long unixSeconds)
  {
    if (unixSeconds <= 0)
    {
      return null;
    }
    try
    {
      return DateTimeOffset.FromUnixTimeSeconds(unixSeconds);
    }
    catch (ArgumentOutOfRangeException)
    {
      return null;
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
