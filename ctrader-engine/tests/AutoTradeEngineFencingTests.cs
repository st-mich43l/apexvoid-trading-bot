using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

// Executor-level proofs for the fenced-lease, route-first and broker-uncertainty
// contracts. These share the harness (fake store/client, candidate builders)
// with AutoTradeEngineTests.
public sealed partial class AutoTradeEngineTests
{
  private const string CandidateId =
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

  // ---------------------------------------------------------------- claiming

  [Fact]
  public async Task RetryableCandidateIsReclaimedAndReprocessed()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    store.SeedCandidateState(CandidateId, CandidateExecutionStates.RetryableError);
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Contains(CandidateId, store.Claims);
    Assert.Single(client.Orders);
    Assert.Contains("candidate_retry_reclaimed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ActiveCandidateLeaseKeepsTheCursorInPlace()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    store.SeedCandidateState(CandidateId, CandidateExecutionStates.Processing);
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Metrics.Contains("duplicate_suppressed"));

    Assert.Empty(client.Orders);
    Assert.Equal("0-0", store.Cursor);
    Assert.DoesNotContain(CandidateId, store.Claims);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData("ordered:91")]
  [InlineData("rejected:stale")]
  [InlineData(CandidateExecutionStates.DryRun)]
  public async Task TerminalCandidateAdvancesTheCursor(string terminal)
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    store.SeedCandidateState(CandidateId, terminal);
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.CursorAdvanced.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal("1-0", store.Cursor);
    Assert.Empty(client.Orders);
    Assert.Contains("candidate_terminal_cursor_advanced", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task UnknownExecutionStateRaisesIntegrityAndHoldsTheCursor()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson());
    store.SeedCandidateState(CandidateId, "half-written-marker");
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "candidate_integrity_error");

    Assert.Equal("0-0", store.Cursor);
    Assert.Empty(client.Orders);
    Assert.Contains("candidate_state_conflict", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // ----------------------------------------------------------------- fencing

  [Fact]
  public async Task LeaseLossBeforePlacementPreventsAnyBrokerMutation()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson())
    {
      LeaseRenewalBlocked = true,
    };
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "candidate_lease_lost");

    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains("executor_lease_lost_before_broker", store.Metrics);
    // A stale executor must not publish a rejection or complete the candidate.
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    Assert.Equal("0-0", store.Cursor);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StaleExecutorCannotCompleteOverASuccessor()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { PauseMarketOrderCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await client.MarketOrderEntered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // The lease expires mid-call. An expired broker_submitting state is
    // recovery-required for normal intake; only the recovery claim policy
    // may take it over.
    store.ExpireLease(CandidateId);
    var successor = await store.TryClaimCandidateAsync(
      CandidateId,
      "1-0",
      TimeSpan.FromMinutes(2),
      cts.Token,
      CandidateClaimPolicy.Recovery
    );
    Assert.Equal(CandidateClaimDisposition.Claimed, successor.Disposition);
    client.ReleaseMarketOrder.TrySetResult(true);

    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("executor_stale_complete_blocked")
      || store.Events.ToArray().Any(item => item.Type == "candidate_lease_lost")
    );

    // Whatever the stale executor did next, it never rewrote the successor's
    // record and never produced a terminal outcome of its own. The successor
    // recovery claim is structurally recovery-owned, never normal processing.
    Assert.Equal(
      CandidateExecutionStates.BrokerReconciling,
      store.CandidateState(CandidateId)
    );
    Assert.False(store.Ordered.Task.IsCompleted);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task HeartbeatKeepsOwnershipAcrossALongBrokerCall()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { PauseMarketOrderCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { })
    {
      // Compress the renewal interval so a "long" broker call is many
      // heartbeats wide without any wall-clock waiting.
      HeartbeatDelay = (_, token) => Task.Delay(10, token),
    };
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await client.MarketOrderEntered.Task.WaitAsync(TimeSpan.FromSeconds(2));
    await WaitUntilAsync(() =>
      store.MetricsSnapshot()
        .Count(metric => metric == "executor_lease_heartbeat_success") >= 3
    );

    // A second executor must not be able to take the candidate while the
    // heartbeat is alive.
    var rival = await store.TryClaimCandidateAsync(
      CandidateId,
      "1-0",
      TimeSpan.FromMinutes(2),
      cts.Token
    );
    Assert.Equal(CandidateClaimDisposition.ActiveElsewhere, rival.Disposition);
    // Ownership was never in doubt while the broker call was in flight.
    Assert.DoesNotContain(
      "executor_lease_heartbeat_failed",
      store.MetricsSnapshot()
    );

    client.ReleaseMarketOrder.TrySetResult(true);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(3));
    await Task.Delay(80, cts.Token);

    Assert.Single(client.Orders);
    // The heartbeat stops with the candidate: renewals stop once it completes.
    var renewalsAtCompletion = store.MetricsSnapshot()
      .Count(metric => metric == "executor_lease_heartbeat_success");
    await Task.Delay(80, cts.Token);
    Assert.Equal(
      renewalsAtCompletion,
      store.MetricsSnapshot()
        .Count(metric => metric == "executor_lease_heartbeat_success")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // ------------------------------------------------------- broker uncertainty

  [Fact]
  public async Task LostMarketResponseIsAdoptedInsteadOfDuplicated()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { LoseMarketResponseCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(5));

    // Exactly one broker request, adopted rather than repeated, and the
    // uncertain state was never downgraded to a normal retry.
    Assert.Single(client.Orders);
    Assert.Contains("broker_outcome_unknown_preserved", store.Metrics);
    Assert.Contains("broker_outcome_adopted", store.Metrics);
    Assert.Contains("broker_duplicate_prevented", store.Metrics);
    Assert.DoesNotContain(
      $"{CandidateExecutionStates.BrokerOutcomeUnknown}"
        + $"->{CandidateExecutionStates.RetryableError}",
      store.Transitions
    );
    Assert.Contains(store.Events, item => item.Type == "broker_outcome_unknown");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ConfirmedBrokerAbsenceRetriesWithoutDuplication()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    // The first request never reaches the broker; the second one succeeds.
    var client = new FakeTradingClient { FailMarketOrderCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(5));

    Assert.Single(client.Orders);
    Assert.Contains("broker_outcome_confirmed_absent", store.Metrics);
    Assert.Contains("broker_recovery_absence_confirmed", store.Metrics);
    Assert.Contains("broker_recovery_empty_snapshot", store.Metrics);
    Assert.True(
      store.Metrics.Count(item => item == "broker_recovery_empty_snapshot") >= 2
    );
    Assert.Contains("candidate_retry_waiting", store.Metrics);
    Assert.Contains("candidate_retry_reclaimed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task DelayedBrokerVisibilityIsAdoptedWithoutDuplicateSubmission()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    // Broker accepts the order, acknowledgement is lost, and the position is
    // invisible on the first reconcile snapshot only.
    var client = new FakeTradingClient
    {
      LoseMarketResponseCall = 1,
      // One empty recovery snapshot, then the accepted position appears.
      HideAcceptedMarketPositionsForReconcileCalls = 1,
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Metrics.Contains("broker_outcome_adopted"));

    Assert.Single(client.Orders);
    Assert.Contains("broker_response_unknown", store.Metrics);
    Assert.Contains("broker_recovery_empty_snapshot", store.Metrics);
    Assert.DoesNotContain("broker_recovery_absence_confirmed", store.Metrics);
    Assert.Contains("broker_duplicate_prevented", store.Metrics);
    Assert.DoesNotContain("candidate_retry_reclaimed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task UnknownPlannedRouteRejectsBeforeAnyBrokerCall()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m),
      plannedRoute: "twap_unknown",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains("final_stop_entry_route_invalid", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SingleLimitMissingLegEntryRejectsBeforeBrokerCall()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m),
      plannedRoute: "single_limit",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [],
      orderTypePreference: "limit",
      entryDistribution: "single"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions() with { ZoneFillEnabled = false },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains("final_stop_leg_entries_missing", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task StopAmendmentUncertaintyBecomesAdoptionNotAReplacement()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { FailAmendmentCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() => store.Metrics.Contains("broker_outcome_adopted"));

    Assert.Single(client.Orders);
    Assert.Contains("final_stop_amendment_unknown", store.Metrics);
    Assert.Contains("broker_duplicate_prevented", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // ------------------------------------------------------------ absolute stop

  [Theory]
  [InlineData(4000.2)]
  [InlineData(4001.4)]
  [InlineData(3999.1)]
  public async Task MarketFillSlippageNeverMovesTheApprovedAbsoluteStop(
    double fillPrice
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var fill = (decimal)fillPrice;
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient();
    client.EnqueueMarketExecutionPrice(fill);
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // 4000.2 planned entry, 6.5 clamped stop distance: the approved absolute
    // stop is 3993.70 regardless of where the fill landed. `fill - distance`
    // would have produced 3994.90 / 3992.60 for the slipped fills.
    var amendment = Assert.Single(client.StopAmendments);
    Assert.Equal(3993.70m, amendment.StopLoss);
    var position = Assert.Single(store.Positions.Values);
    Assert.Equal(3993.70m, position.CurrentStopLoss);
    Assert.Equal(3993.70m, position.InitialStopLoss);
    Assert.Contains("final_stop_absolute_applied", store.Metrics);

    var slippage = store.Events
      .Where(item => item.Type == "execution_slippage")
      .ToArray();
    if (fill == 4000.2m)
    {
      Assert.Empty(slippage);
    }
    else
    {
      // Slippage is reported as observed risk, never absorbed by the stop.
      var observed = Assert.IsType<decimal>(Assert.Single(slippage).StopPips);
      Assert.Equal(Math.Abs(fill - 3993.70m) / 0.1m, observed);
      Assert.NotEqual(65m, observed);
    }

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task SellMarketFillSlippageAlsoKeepsTheApprovedAbsoluteStop()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson(direction: "SELL"));
    var client = new FakeTradingClient();
    client.EnqueueMarketExecutionPrice(3999.1m);
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // SELL transacts at the bid (4000.00) and the approved stop sits 6.5 above
    // it. A fill 9 pips lower must not drag the stop down with it.
    var amendment = Assert.Single(client.StopAmendments);
    Assert.Equal(4006.50m, amendment.StopLoss);
    Assert.Equal(
      4006.50m,
      Assert.Single(store.Positions.Values).CurrentStopLoss
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // -------------------------------------------------------------- route first

  [Fact]
  public async Task SingleLimitStopContractIsValidatedAtTheLimitPrice()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Python priced the stop at the limit it also published as planned entry.
    var contract = PlannedStopContract(3999.5m);
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      contract,
      plannedRoute: "single_limit",
      plannedEntryPrice: 3999.5m,
      legEntryPrices: [3999.5m],
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3998.5m,
      entryHigh: 3999.5m,
      stopPlanVersion: 2
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    var order = Assert.Single(client.LimitOrders);
    Assert.Equal(3999.5m, order.LimitPrice);
    // Stop distance is measured from the limit price to the approved stop.
    Assert.Equal(
      decimal.ToInt64((3999.5m - contract.StopLoss) * 100_000m),
      order.RelativeStopLoss
    );
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RestingStopContractStillRejectsPublishEntryDrift()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // The entry contract names the exact resting limit, but Python priced the
    // stop at a different entry. The resting stop contract remains strict.
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m, structureSwing: 3995.5m),
      plannedRoute: "single_limit",
      plannedEntryPrice: 3999.5m,
      legEntryPrices: [3999.5m],
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3998.5m,
      entryHigh: 3999.5m,
      structureSwing: 3995.5m,
      stopPlanVersion: 2
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.LimitOrders);
    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_protective_stop_contract_mismatch")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RestingEntryContractStillRejectsPublishEntryDrift()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(3999.5m),
      plannedRoute: "single_limit",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "limit",
      entryDistribution: "single",
      entryLow: 3998.5m,
      entryHigh: 3999.5m
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.LimitOrders);
    Assert.Empty(client.Orders);
    Assert.Contains("final_stop_entry_drift_rejected", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task RouteMismatchRejectsBeforeAnyBrokerCall()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Python committed to zone_split; the executor cannot split (capability
    // off) and resolves market instead.
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m),
      plannedRoute: "zone_split",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "either",
      entryDistribution: "either"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions() with { ZoneFillEnabled = false },
      store,
      () => Now,
      _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains("final_stop_entry_route_mismatch", store.Metrics);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_stop_entry_route_mismatch")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MarketEntryAndStopContractsAcceptLiveDriftInsideTheZone()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const decimal structureSwing = 3995.5m;
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m, structureSwing: structureSwing),
      plannedRoute: "market",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single",
      entryHigh: 4001.2m,
      structureSwing: structureSwing,
      stopPlanVersion: 2
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.7m, 4000.9m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    var recomputed = PlannedStopContract(
      4000.9m,
      structureSwing: structureSwing
    ).Plan;
    Assert.Equal(
      recomputed.StopLoss,
      Assert.Single(client.StopAmendments).StopLoss
    );
    Assert.Contains("entry_contract_market_drift_observed", store.Metrics);
    Assert.Contains("stop_contract_market_entry_recomputed", store.Metrics);
    Assert.DoesNotContain("final_stop_entry_drift_rejected", store.Metrics);
    Assert.DoesNotContain(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_protective_stop_contract_mismatch")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MarketV1StopContractAlsoAcceptsEntryRelativeDrift()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const decimal structureSwing = 3995.5m;
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m, structureSwing: structureSwing),
      plannedRoute: "market",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single",
      entryHigh: 4001.2m,
      structureSwing: structureSwing
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.7m, 4000.9m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MarketStructuralStopIdentityMismatchStillRejects()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const decimal structureSwing = 3995.5m;
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m, structureSwing: structureSwing),
      plannedRoute: "market",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single",
      entryHigh: 4001.2m,
      structureSwing: structureSwing,
      stopPlanVersion: 2,
      stopSourceOverride: "wick"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.7m, 4000.9m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_protective_stop_contract_mismatch")
    );
    Assert.DoesNotContain("stop_contract_market_entry_recomputed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MarketOutsideZoneStillRejectsAtTheEntryDistanceGuard()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    const decimal structureSwing = 3995.5m;
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m, structureSwing: structureSwing),
      plannedRoute: "market",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single",
      entryHigh: 4001.2m,
      structureSwing: structureSwing,
      stopPlanVersion: 2
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4002.1m, 4002.3m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("entry distance rejected")
    );
    Assert.DoesNotContain("entry_contract_market_drift_observed", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MarketRouteInsideToleranceStillSubmits()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m),
      plannedRoute: "market",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.DoesNotContain("final_stop_entry_route_mismatch", store.Metrics);
    Assert.DoesNotContain("final_stop_entry_drift_rejected", store.Metrics);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  [InlineData(TradeDirection.Buy, 4000.0, 4000.0)]
  [InlineData(TradeDirection.Sell, 4000.0, 4000.0)]
  [InlineData(TradeDirection.Buy, 4000.1, 4000.0)]
  [InlineData(TradeDirection.Sell, 3999.9, 4000.0)]
  public void StopOnWrongSideUsesTheDedicatedMetric(
    TradeDirection direction,
    double stopLoss,
    double executableEntry
  )
  {
    var rejection = AutoTradeEngine.FinalStopSideRejection(
      direction,
      (decimal)stopLoss,
      (decimal)executableEntry
    );

    Assert.NotNull(rejection);
    Assert.Equal("final_stop_not_on_losing_side", rejection.Value.Metric);
    Assert.Equal(
      "final_stop_not_on_losing_side_of_executable_entry",
      rejection.Value.Reason
    );
    Assert.NotEqual("final_stop_entry_drift_rejected", rejection.Value.Metric);
  }

  // -------------------------------------------------------- zone identity

  [Fact]
  public async Task WrongOpposingZoneIdRejectsThePushedStop()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PushedStopCandidateJson(
      zoneIdOverride: "supply:M15:0000.00:0001.00:1"
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_stop_zone_identity_mismatch")
    );
    Assert.Contains("final_stop_zone_identity_mismatch", store.MetricsSnapshot());

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MissingOpposingZoneIdRejectsThePushedStop()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PushedStopCandidateJson(
      dropZoneId: true
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    // A pushed stop with no zone identity is a mismatch, never an implicit
    // pass, so it must fail on identity rather than on geometry.
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_stop_zone_identity_mismatch")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Theory]
  // Same identity, geometry moved by more than a tick on one edge: the zone
  // Python approved is not the zone the executor measured.
  [InlineData(3995.0, null)]
  [InlineData(null, 3996.9)]
  public async Task ShiftedOpposingZoneGeometryRejectsThePushedStop(
    double? lowOverride,
    double? highOverride
  )
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PushedStopCandidateJson(
      zoneLowOverride: lowOverride is null ? null : (decimal)lowOverride.Value,
      zoneHighOverride: highOverride is null ? null : (decimal)highOverride.Value
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);
    Assert.Contains(store.Events, item =>
      item.Type == "rejected"
      && item.Message.Contains("final_stop_zone_identity_mismatch")
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task ContextOnlyZoneNeedsNoPushIdentity()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // The zone is carried for context but is not execution-grade, so neither
    // side pushes the stop and no identity is required to validate it.
    var store = new FakeAutoTradeStore(PushedStopCandidateJson(
      executionGrade: false,
      dropZoneId: true
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");
    Assert.DoesNotContain(
      "final_stop_zone_identity_mismatch", store.MetricsSnapshot()
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task NoAdjustmentContractCannotValidateAgainstAPushedPlan()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    // Python claims it never pushed, while the executor's own planner does
    // push beyond the same execution-grade zone.
    var store = new FakeAutoTradeStore(PushedStopCandidateJson(
      claimNoAdjustment: true
    ));
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "rejected");

    Assert.Empty(client.Orders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task MatchingOpposingZoneIdentityAcceptsThePushedStop()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(PushedStopCandidateJson());
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Single(client.Orders);
    Assert.DoesNotContain(store.Events, item => item.Type == "rejected");

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // ----------------------------------------------------------------- lifecycle

  [Fact]
  public async Task RetryableLifecycleStateRecoversToFilled()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    // The first attempt fails before any broker contact; the retry succeeds.
    // Call 1 is the session-startup snapshot; call 2 is this candidate's
    // pre-submit read.
    var client = new FakeTradingClient { FailAccountCall = call => call == 2 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(5));

    Assert.Contains("lifecycle_retryable_error", store.Metrics);
    Assert.Contains(
      store.LifecycleEvents,
      item => item.Type == "candidate_retryable_error"
    );
    Assert.Contains("candidate_retry_reclaimed", store.Metrics);
    // The retryable error is nonterminal, so every transition the retry makes
    // afterwards is accepted instead of being refused as backward.
    Assert.DoesNotContain("lifecycle_invalid_transition", store.Metrics);
    var types = store.LifecycleEvents.Select(item => item.Type).ToArray();
    Assert.True(
      Array.IndexOf(types, "candidate_retryable_error")
        < Array.IndexOf(types, "order_accepted")
    );
    var state = LifecycleRecordFor(store, CandidateId);
    Assert.Equal("managing", state?.State);
    Assert.False(state?.Terminal);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task BrokerUnknownLifecycleRecoversThroughAdoption()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    var client = new FakeTradingClient { LoseMarketResponseCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "order_filled");

    Assert.Contains(
      store.LifecycleEvents,
      item => item.Type == "broker_outcome_unknown"
    );
    Assert.Contains("lifecycle_broker_recovery", store.Metrics);
    // Recovery-required is nonterminal, so adoption may still advance the
    // lifecycle to a filled position.
    Assert.DoesNotContain("lifecycle_invalid_transition", store.Metrics);
    var types = store.LifecycleEvents.Select(item => item.Type).ToArray();
    Assert.True(
      Array.IndexOf(types, "broker_outcome_unknown")
        < Array.IndexOf(types, "order_filled")
    );
    var state = LifecycleRecordFor(store, CandidateId);
    Assert.False(state?.Terminal);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  [Fact]
  public async Task LeaseLossTelemetryDoesNotCreateCandidateLifecycleState()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    var store = new FakeAutoTradeStore(CandidateJson())
    {
      LeaseRenewalBlocked = true,
    };
    var client = new FakeTradingClient();
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitForEventAsync(store, "candidate_lease_lost");

    var state = LifecycleRecordFor(store, CandidateId);
    // Ownership-loss telemetry is history only: it must not overwrite the
    // lifecycle snapshot the current owner is responsible for.
    Assert.NotEqual("candidate_lease_lost", state?.State);
    Assert.False(state?.Terminal ?? false);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // ------------------------------------------------------------------ helpers

  private static AutoTradeLifecycleStateRecord? LifecycleRecordFor(
    FakeAutoTradeStore store,
    string owner
  ) => store.Values.TryGetValue(
      $"auto_trade:lifecycle_state:{owner}",
      out var raw
    )
    ? JsonSerializer.Deserialize(
      raw,
      RedisJsonContext.Default.AutoTradeLifecycleStateRecord
    )
    : null;

  private sealed record StopContract(decimal Entry, StructureStopPlan Plan)
  {
    public decimal StopLoss => Plan.StopLoss;
  }

  // Recomputes the executor's own planner at a chosen entry so a candidate can
  // carry a stop contract that is exact by construction. The test then only
  // varies *which* entry the contract was priced at.
  private static StopContract PlannedStopContract(
    decimal contractEntry,
    string direction = "BUY",
    decimal structureSwing = 3993.5m,
    decimal atr = 1.0m,
    OpposingZoneStopContext? zone = null
  ) => new(
    contractEntry,
    StructureStopPlanner.Plan(
      direction == "BUY" ? TradeDirection.Buy : TradeDirection.Sell,
      contractEntry,
      structureSwing,
      atr,
      0.3m,
      null,
      0.15m,
      40,
      60,
      0.1m,
      Symbol,
      zone
    )
  );

  private static string PlannedCandidateJson(
    StopContract contract,
    string? plannedRoute,
    decimal? plannedEntryPrice,
    decimal[]? legEntryPrices = null,
    string direction = "BUY",
    string? orderTypePreference = null,
    string? entryDistribution = null,
    decimal entryLow = 3999.5m,
    decimal entryHigh = 4000.5m,
    decimal structureSwing = 3993.5m,
    decimal atr = 1.0m,
    int stopPlanVersion = 1,
    string? stopSourceOverride = null
  ) => JsonSerializer.Serialize(new
  {
    version = 5,
    candidate_id = new string('a', 64),
    symbol = "XAU",
    timeframe = "M1",
    setup = "Trend Pullback",
    mode = "auto_trend_pullback",
    direction,
    trigger_ts = "1000",
    created_at = 1_000,
    spot_ts = 1_000,
    current_price = 4000.1,
    key_level = 4000.0,
    entry_zone = new { low = entryLow, high = entryHigh },
    confluence = 2,
    reasons = new[] { "trend pullback into displacement origin zone" },
    bar_ts = 1_000,
    atr,
    structure_swing = structureSwing,
    targets_pips = new[] { 30, 60, 90 },
    displacement_direction = direction == "BUY" ? "up" : "down",
    displacement_age_bars = 1,
    bos_direction = direction == "BUY" ? "up" : "down",
    bos_ts = 1_000,
    opposing_level_distance_atr = 2.0,
    risk_multiplier = 1.0m,
    order_type_preference = orderTypePreference,
    entry_distribution = entryDistribution,
    regime = "trend",
    planned_stop_entry_price = contract.Entry,
    planned_stop_price = contract.Plan.StopLoss,
    planned_stop_distance = contract.Plan.Distance,
    planned_stop_pips = contract.Plan.StopPips,
    planned_stop_raw_price = contract.Plan.RawStopLoss,
    planned_stop_clamped = contract.Plan.Clamped,
    stop_source = stopSourceOverride ?? contract.Plan.Source,
    stop_plan_version = stopPlanVersion,
    planned_base_stop_price = contract.Plan.BaseStopLoss
      ?? contract.Plan.StopLoss,
    planned_base_stop_pips = contract.Plan.BaseStopPips
      ?? contract.Plan.StopPips,
    planned_final_stop_price = contract.Plan.StopLoss,
    planned_final_stop_distance = contract.Plan.Distance,
    planned_final_stop_pips = contract.Plan.StopPips,
    stop_adjustment = contract.Plan.Adjustment,
    stop_adjustment_zone_id = contract.Plan.AdjustmentZoneId,
    stop_adjustment_zone_low = contract.Plan.AdjustmentZoneLow,
    stop_adjustment_zone_high = contract.Plan.AdjustmentZoneHigh,
    planned_execution_route = plannedRoute,
    planned_entry_price = plannedEntryPrice,
    planned_leg_entry_prices = legEntryPrices,
    entry_plan_version = 1,
  });

  // A v5/stop-plan-v2 candidate whose stop was pushed beyond an opposing zone,
  // carrying the exact zone identity the executor derives for the same zone.
  // `zoneIdOverride`/`dropZoneId` change only the identity Python claims it
  // pushed beyond. The zone geometry the executor sees is unchanged, so the
  // executor recomputes the same push and the identities must line up exactly.
  private static string PushedStopCandidateJson(
    string? zoneIdOverride = null,
    bool dropZoneId = false,
    decimal? zoneLowOverride = null,
    decimal? zoneHighOverride = null,
    bool executionGrade = true,
    bool claimNoAdjustment = false
  )
  {
    // The base structure stop lands inside this zone, and pushing beyond it
    // still fits the stop envelope, so the contract really is a pushed stop.
    // A context-only zone is simply too wide to trade against, which is how
    // the executor classifies it, so widening it drops the push entirely.
    const decimal structureSwing = 3996.5m;
    var zoneLow = executionGrade ? 3996.0m : 3980.0m;
    const decimal zoneHigh = 3996.4m;
    var identity = AutoTradeEngine.OpposingZoneFingerprint(
      "XAU",
      "M1",
      "BUY",
      zoneLow,
      zoneHigh,
      1_000,
      null
    );
    var plan = PlannedStopContract(
      4000.2m,
      structureSwing: structureSwing,
      // `claimNoAdjustment` prices the contract as if no zone existed, so the
      // published plan says "none" while the executor still pushes.
      zone: claimNoAdjustment || !executionGrade
        ? null
        : new OpposingZoneStopContext(
          identity,
          zoneLow,
          zoneHigh,
          true,
          true,
          0.3m
        )
    ).Plan;
    return JsonSerializer.Serialize(new
    {
      version = 5,
      candidate_id = new string('a', 64),
      symbol = "XAU",
      timeframe = "M1",
      setup = "Trend Pullback",
      mode = "auto_trend_pullback",
      direction = "BUY",
      trigger_ts = "1000",
      created_at = 1_000,
      spot_ts = 1_000,
      current_price = 4000.1,
      key_level = 4000.0,
      entry_zone = new { low = 3999.5m, high = 4000.5m },
      confluence = 2,
      reasons = new[] { "trend pullback into displacement origin zone" },
      bar_ts = 1_000,
      atr = 1.0m,
      structure_swing = structureSwing,
      targets_pips = new[] { 30, 60, 90 },
      displacement_direction = "up",
      displacement_age_bars = 1,
      bos_direction = "up",
      bos_ts = 1_000,
      opposing_level_distance_atr = 2.0,
      risk_multiplier = 1.0m,
      regime = "trend",
      opposing_zone_low = zoneLow,
      opposing_zone_high = zoneHigh,
      opposing_zone_id = identity,
      planned_stop_entry_price = 4000.2m,
      planned_stop_raw_price = plan.RawStopLoss,
      planned_stop_clamped = plan.Clamped,
      stop_source = plan.Source,
      stop_plan_version = 2,
      planned_base_stop_price = plan.BaseStopLoss ?? plan.StopLoss,
      planned_base_stop_pips = plan.BaseStopPips ?? plan.StopPips,
      planned_final_stop_price = plan.StopLoss,
      planned_final_stop_distance = plan.Distance,
      planned_final_stop_pips = plan.StopPips,
      stop_adjustment = plan.Adjustment,
      stop_adjustment_zone_id = dropZoneId
        ? null
        : zoneIdOverride ?? plan.AdjustmentZoneId,
      stop_adjustment_zone_low = zoneLowOverride ?? plan.AdjustmentZoneLow,
      stop_adjustment_zone_high = zoneHighOverride ?? plan.AdjustmentZoneHigh,
      planned_execution_route = "market",
      planned_entry_price = 4000.2m,
      planned_leg_entry_prices = new[] { 4000.2m },
      entry_plan_version = 1,
    });
  }
}
