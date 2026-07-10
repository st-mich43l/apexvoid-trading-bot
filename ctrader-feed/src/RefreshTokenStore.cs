using OpenAPI.Net;

namespace ApexVoid.CTraderFeed;

public interface IRefreshTokenStore
{
  Task<string?> GetAsync(CancellationToken cancellationToken);
  Task SetAsync(string token, CancellationToken cancellationToken);
}

public interface IRedisStringCommands
{
  Task<string?> GetStringAsync(string key, CancellationToken cancellationToken);
  Task SetStringAsync(string key, string value, CancellationToken cancellationToken);
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
}

public sealed class RefreshTokenState(
  FeedOptions options,
  IRefreshTokenStore store
)
{
  public string AccessToken { get; private set; } = options.AccessToken;
  public string RefreshToken { get; private set; } = options.RefreshToken;

  public async Task SeedAsync(CancellationToken cancellationToken)
  {
    var persisted = await store.GetAsync(cancellationToken);
    if (!string.IsNullOrWhiteSpace(persisted))
    {
      RefreshToken = persisted;
    }
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
      await store.SetAsync(RefreshToken, cancellationToken);
    }
  }
}
