using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

public sealed class AutoTradeLifecycleTests
{
  [Theory]
  [InlineData("warning", null)]
  [InlineData("account_capability", null)]
  [InlineData("ready", null)]
  [InlineData("range_flip_attempted", null)]
  [InlineData("stop_moved", null)]
  [InlineData("executor_received", "executor_received")]
  [InlineData("order_submitted", "order_submitted")]
  [InlineData("opened", "order_filled")]
  [InlineData("managing", "managing")]
  [InlineData("rejected", "rejected")]
  public void TransitionForEventMapsOnlyExplicitLifecycleEvents(
    string type,
    string? expectedState
  )
  {
    var transition = AutoTradeLifecycle.TransitionForEvent(type, null);
    if (expectedState is null)
    {
      Assert.Null(transition);
      return;
    }
    Assert.NotNull(transition);
    Assert.True(transition.MutatesCurrentState);
    Assert.Equal(expectedState, transition.State);
  }

  [Fact]
  public void TakeProfitUsesRemainingVolumeForPartialOrClosed()
  {
    var partial = AutoTradeLifecycle.TransitionForEvent("take_profit", 500);
    Assert.NotNull(partial);
    Assert.Equal("partially_closed", partial.State);
    Assert.False(partial.Terminal);

    var closed = AutoTradeLifecycle.TransitionForEvent("take_profit", 0);
    Assert.NotNull(closed);
    Assert.Equal("closed", closed.State);
    Assert.True(closed.Terminal);
  }

  [Theory]
  [InlineData("closed", "managing", LifecycleTransitionOutcome.Invalid)]
  [InlineData("rejected", "order_submitted", LifecycleTransitionOutcome.Invalid)]
  [InlineData("order_filled", "routing_selected", LifecycleTransitionOutcome.Invalid)]
  [InlineData("closed", "closed", LifecycleTransitionOutcome.Duplicate)]
  [InlineData("order_submitted", "order_filled", LifecycleTransitionOutcome.Recovery)]
  [InlineData(null, "managing", LifecycleTransitionOutcome.Applied)]
  public void EvaluateTransitionEnforcesOrderingAndRecovery(
    string? current,
    string target,
    LifecycleTransitionOutcome expected
  )
  {
    var transition = new LifecycleTransition(true, target, false);
    var outcome = AutoTradeLifecycle.EvaluateTransition(
      current,
      transition,
      "test"
    );
    Assert.Equal(expected, outcome.Outcome);
  }

  [Fact]
  public void ParseStateSupportsPlainStringAndStructuredRecord()
  {
    Assert.Equal("managing", AutoTradeLifecycle.ParseState("managing"));
    Assert.Equal(
      "order_filled",
      AutoTradeLifecycle.ParseState(
        """{"owner_id":"abc","state":"order_filled","version":1}"""
      )
    );
  }

  [Theory]
  [InlineData("warning", null)]
  [InlineData("managing", "MANAGING")]
  [InlineData("order_filled", "ORDER_FILLED")]
  [InlineData("rejected", "REARMED")]
  public void RangeSideStateForSkipsUnmappedStates(
    string lifecycleState,
    string? expectedRail
  )
  {
    Assert.Equal(expectedRail, AutoTradeLifecycle.RangeSideStateFor(lifecycleState));
  }
}
