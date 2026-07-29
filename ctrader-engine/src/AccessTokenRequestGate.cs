namespace ApexVoid.CTraderFeed;

// Reads the access token only after refresh and token-bound requests are serialized.
internal sealed class AccessTokenRequestGate(
  SemaphoreSlim requestLock,
  Func<string> readAccessToken
)
{
  public async Task<T> RunAsync<T>(
    Func<string, Task<T>> operation,
    CancellationToken cancellationToken
  )
  {
    await requestLock.WaitAsync(cancellationToken);
    try
    {
      return await operation(readAccessToken());
    }
    finally
    {
      requestLock.Release();
    }
  }
}
