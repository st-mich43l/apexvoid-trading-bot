using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class AccountAuthorizationFlowTests
{
  [Fact]
  public async Task RejectedAccessRefreshesAndAuthorizesExactlyOnce()
  {
    var calls = new List<string>();

    await AccountAuthorizationFlow.RunAsync(
      _ =>
      {
        calls.Add("lookup");
        return Task.FromException<string>(
          new InvalidOperationException("INVALID_TOKEN")
        );
      },
      _ => calls.Add("accept"),
      _ =>
      {
        calls.Add("refresh+auth");
        return Task.CompletedTask;
      },
      _ =>
      {
        calls.Add("auth");
        return Task.CompletedTask;
      },
      _ => { },
      CancellationToken.None
    );

    Assert.Equal(["lookup", "refresh+auth"], calls);
  }

  [Fact]
  public async Task ValidAccessUsesTheInitialAuthorizationPath()
  {
    var calls = new List<string>();

    await AccountAuthorizationFlow.RunAsync(
      _ =>
      {
        calls.Add("lookup");
        return Task.FromResult("accounts");
      },
      _ => calls.Add("accept"),
      _ =>
      {
        calls.Add("refresh+auth");
        return Task.CompletedTask;
      },
      _ =>
      {
        calls.Add("auth");
        return Task.CompletedTask;
      },
      _ => { },
      CancellationToken.None
    );

    Assert.Equal(["lookup", "accept", "auth"], calls);
  }
}
