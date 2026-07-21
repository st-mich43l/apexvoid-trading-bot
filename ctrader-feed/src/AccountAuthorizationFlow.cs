namespace ApexVoid.CTraderFeed;

internal static class AccountAuthorizationFlow
{
  public static async Task RunAsync<TAccounts>(
    Func<CancellationToken, Task<TAccounts>> getAccounts,
    Action<TAccounts> acceptAccounts,
    Func<CancellationToken, Task> refreshAndAuthorize,
    Func<CancellationToken, Task> authorizeAccount,
    Action<string> log,
    CancellationToken cancellationToken
  )
  {
    TAccounts accounts;
    try
    {
      accounts = await getAccounts(cancellationToken);
    }
    catch (InvalidOperationException)
    {
      log("configured access token rejected; refreshing before one auth retry");
      await refreshAndAuthorize(cancellationToken);
      return;
    }

    acceptAccounts(accounts);
    await authorizeAccount(cancellationToken);
  }
}
