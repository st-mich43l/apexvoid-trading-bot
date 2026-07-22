namespace ApexVoid.CTraderFeed;

internal sealed record TokenExpiryResolution(
  DateTimeOffset ExpiresAt,
  string Interpretation,
  bool Warning,
  string? WarningMessage = null
);

internal static class TokenExpiry
{
  private static readonly TimeSpan MinimumLifetime = TimeSpan.FromDays(1);
  private static readonly TimeSpan MaximumLifetime = TimeSpan.FromDays(60);
  private static readonly TimeSpan FallbackLifetime = TimeSpan.FromDays(30);

  public static TokenExpiryResolution ResolveExpiry(
    long rawExpiresIn,
    DateTimeOffset now
  )
  {
    var relative = TryRelative(rawExpiresIn, now);
    var absolute = TryAbsolute(rawExpiresIn);
    var relativePlausible = IsPlausible(relative, now);
    var absolutePlausible = IsPlausible(absolute, now);

    if (relativePlausible && absolutePlausible)
    {
      return new TokenExpiryResolution(
        absolute!.Value,
        "absolute-ms",
        true,
        "both seconds-remaining and absolute-ms interpretations are plausible; preferring absolute-ms"
      );
    }
    if (absolutePlausible)
    {
      return new TokenExpiryResolution(absolute!.Value, "absolute-ms", false);
    }
    if (relativePlausible)
    {
      return new TokenExpiryResolution(relative!.Value, "seconds-remaining", false);
    }
    return new TokenExpiryResolution(
      now + FallbackLifetime,
      "fallback-30d",
      true,
      "neither seconds-remaining nor absolute-ms interpretation is plausible; using 30-day fallback"
    );
  }

  private static DateTimeOffset? TryRelative(long rawExpiresIn, DateTimeOffset now)
  {
    try
    {
      return now + TimeSpan.FromSeconds(rawExpiresIn);
    }
    catch (ArgumentOutOfRangeException)
    {
      return null;
    }
    catch (OverflowException)
    {
      return null;
    }
  }

  private static DateTimeOffset? TryAbsolute(long rawExpiresIn)
  {
    try
    {
      return DateTimeOffset.FromUnixTimeMilliseconds(rawExpiresIn);
    }
    catch (ArgumentOutOfRangeException)
    {
      return null;
    }
  }

  private static bool IsPlausible(DateTimeOffset? candidate, DateTimeOffset now) =>
    candidate is not null
    && candidate.Value >= now + MinimumLifetime
    && candidate.Value <= now + MaximumLifetime;
}

public sealed record TokenLifecycleStatus(
  string Tier,
  DateTimeOffset? ExpiresAt,
  string SeedFingerprint
)
{
  public static TokenLifecycleStatus Unknown { get; } = new(
    "unknown",
    null,
    "unknown"
  );
}

internal static class TokenRefreshPolicy
{
  public static bool ShouldRefresh(
    DateTimeOffset? expiresAt,
    DateTimeOffset now,
    TimeSpan lead
  ) => expiresAt is null || now >= expiresAt.Value - lead;

  public static TimeSpan FailureBackoff(int attempt)
  {
    var exponent = Math.Max(0, Math.Min(attempt - 1, 5));
    var minutes = 15 * Math.Pow(2, exponent);
    return TimeSpan.FromMinutes(Math.Min(360, minutes));
  }
}

internal sealed class SingleFlightOperation
{
  private readonly object _gate = new();
  private TaskCompletionSource<bool>? _current;

  public Task RunAsync(
    Func<CancellationToken, Task> operation,
    CancellationToken cancellationToken
  )
  {
    TaskCompletionSource<bool> source;
    lock (_gate)
    {
      if (_current is not null)
      {
        return _current.Task.WaitAsync(cancellationToken);
      }
      source = new TaskCompletionSource<bool>(
        TaskCreationOptions.RunContinuationsAsynchronously
      );
      _current = source;
    }
    _ = ExecuteAsync(source, operation, cancellationToken);
    return source.Task.WaitAsync(cancellationToken);
  }

  private async Task ExecuteAsync(
    TaskCompletionSource<bool> source,
    Func<CancellationToken, Task> operation,
    CancellationToken cancellationToken
  )
  {
    try
    {
      await operation(cancellationToken);
      source.TrySetResult(true);
    }
    catch (OperationCanceledException exception)
    {
      source.TrySetCanceled(exception.CancellationToken);
    }
    catch (Exception exception)
    {
      source.TrySetException(exception);
    }
    finally
    {
      lock (_gate)
      {
        if (ReferenceEquals(_current, source))
        {
          _current = null;
        }
      }
    }
  }
}

public sealed class TokenEventNotifier(
  Func<string, string, CancellationToken, Task>? notify,
  Func<DateTimeOffset>? clock = null,
  Action<string>? warningLog = null
)
{
  private static readonly TimeSpan MinimumInterval = TimeSpan.FromHours(1);
  private readonly object _gate = new();
  private readonly Dictionary<string, DateTimeOffset> _lastSent = new(
    StringComparer.Ordinal
  );

  public async Task NotifyAsync(
    string eventType,
    string message,
    CancellationToken cancellationToken
  )
  {
    if (notify is null)
    {
      return;
    }
    var now = (clock ?? (() => DateTimeOffset.UtcNow))();
    lock (_gate)
    {
      if (
        _lastSent.TryGetValue(eventType, out var last)
        && now - last < MinimumInterval
      )
      {
        return;
      }
      _lastSent[eventType] = now;
    }
    try
    {
      await notify("error", message, cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      (warningLog ?? Log)(
        $"token event notification failed: {exception.GetType().Name}: {exception.Message}"
      );
    }
  }

  private static void Log(string message) =>
    Console.Error.WriteLine($"ctrader-feed WARNING {message}");
}

internal static class TokenRedaction
{
  public static string Redact(string message, params string?[] secrets)
  {
    var result = message;
    foreach (var secret in secrets)
    {
      if (!string.IsNullOrWhiteSpace(secret))
      {
        result = result.Replace(secret, "[redacted]", StringComparison.Ordinal);
      }
    }
    return result;
  }
}
