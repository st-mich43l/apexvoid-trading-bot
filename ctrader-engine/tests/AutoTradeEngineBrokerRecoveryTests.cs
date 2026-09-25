using System.Text.Json;
using ApexVoid.CTraderFeed;

namespace CTraderFeed.Tests;

// Broker-recovery regression scenarios: recovery-only state survival across
// crashes, durably time-separated absence confirmations, resolved-route
// identity persistence, and recovery ownership loss. These share the harness
// (fake store/client, candidate builders) with AutoTradeEngineTests.
public sealed partial class AutoTradeEngineTests
{
  private static string ExpectedRootClientOrderId =>
    $"av-{CandidateId[..40]}";

  private static AutoTradeGroupPlan? GroupPlanOf(FakeAutoTradeStore store)
  {
    var raw = store.Values
      .Where(pair => pair.Key.StartsWith(
        "auto_trade:group_plan:",
        StringComparison.Ordinal
      ))
      .Select(pair => pair.Value)
      .SingleOrDefault();
    return raw is null
      ? null
      : JsonSerializer.Deserialize(
        raw,
        RedisJsonContext.Default.AutoTradeGroupPlan
      );
  }

  // Scenario A: a recovery worker crashed and its lease expired, leaving the
  // record in broker_reconciling. Normal intake must classify the candidate as
  // recovery-required and send zero broker requests; the resumed recovery
  // owner adopts the now-visible order instead of submitting another one.
  [Fact]
  public async Task BrokerRecoveryCrashResumesRecoveryOnlyAndAdoptsWithoutResubmission()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson());
    store.SeedCandidateState(
      CandidateId,
      CandidateExecutionStates.BrokerReconciling
    );
    var client = new FakeTradingClient();
    // The order from the pre-crash submission is now visible at the broker,
    // carrying the exact deterministic client order identity.
    client.SeedPosition(new TradingPosition(
      9001,
      Symbol.SymbolId,
      TradeDirection.Buy,
      100,
      4000.2m,
      3995.0m,
      "apexvoid-auto",
      $"apexvoid {CandidateId[..10]}",
      ExpectedRootClientOrderId
    ));
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { });
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(5));

    // Zero new broker submissions: the total across both lives remains one.
    Assert.Empty(client.Orders);
    Assert.Empty(client.LimitOrders);
    Assert.Contains("broker_recovery_started", store.Metrics);
    Assert.Contains("broker_recovery_direct_lookup", store.Metrics);
    Assert.Contains("broker_outcome_adopted", store.Metrics);
    Assert.Contains("broker_duplicate_prevented", store.Metrics);
    // The crashed recovery state never decayed into a normal retry.
    Assert.DoesNotContain("candidate_retry_reclaimed", store.Metrics);
    Assert.DoesNotContain(
      $"{CandidateExecutionStates.BrokerReconciling}"
        + $"->{CandidateExecutionStates.RetryableError}",
      store.Transitions
    );

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // Scenario B: persisted absence confirmations are durably time-separated on
  // the authoritative clock. Immediate re-checks are deferred without
  // advancing quorum; only once the interval elapses does the count reach the
  // quorum and hand the candidate back for one retry.
  [Fact]
  public async Task BrokerRecoveryAbsenceQuorumRequiresDurablySeparatedConfirmations()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson())
    {
      // Freeze the authoritative clock: snapshots arrive "immediately".
      AutoAdvanceAbsenceClock = false,
    };
    // The first request never reaches the broker; the retry succeeds.
    var client = new FakeTradingClient { FailMarketOrderCall = 1 };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { })
    {
      RecoveryDelay = (_, token) => Task.Delay(10, token),
    };
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);

    // First empty snapshot records confirmation one; every immediate snapshot
    // after it is deferred and the durable count does not move.
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("broker_recovery_confirmation_recorded")
    );
    await WaitUntilAsync(() =>
      store.MetricsSnapshot()
        .Count(metric => metric == "broker_recovery_confirmation_deferred") >= 2
    );
    Assert.Equal(1, store.AbsenceProgressFor(CandidateId)?.Confirmations);
    Assert.DoesNotContain(
      "broker_recovery_absence_confirmed",
      store.MetricsSnapshot()
    );
    Assert.False(store.Ordered.Task.IsCompleted);

    // The configured interval elapses on the authoritative clock: the next
    // empty snapshot counts, quorum is reached, and exactly one retry runs.
    store.AbsenceClockSeconds += Options().BrokerAbsenceRecheckSeconds;
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(5));

    var metrics = store.MetricsSnapshot();
    Assert.Contains("broker_recovery_absence_confirmed", metrics);
    Assert.Contains("broker_outcome_confirmed_absent", metrics);
    Assert.Contains("candidate_retry_reclaimed", metrics);
    Assert.Single(client.Orders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // Either resolving to market persists the exact root identity before the
  // broker call so a lost acknowledgement is always recoverable.
  [Fact]
  public async Task BrokerRecoveryEitherRouteMarketPersistsExactRootIdentity()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(PlannedCandidateJson(
      PlannedStopContract(4000.2m),
      plannedRoute: "either",
      plannedEntryPrice: 4000.2m,
      legEntryPrices: [4000.2m],
      orderTypePreference: "market",
      entryDistribution: "single"
    ));
    var client = new FakeTradingClient { PauseMarketOrderCall = 1 };
    var engine = new AutoTradeEngine(
      DemoEvalOptions(), store, () => Now, _ => { }
    );
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await client.MarketOrderEntered.Task.WaitAsync(TimeSpan.FromSeconds(2));

    // The broker call is in flight and the plan already exists with the
    // resolved route and the exact submitted identity.
    var plan = GroupPlanOf(store);
    Assert.NotNull(plan);
    Assert.Equal("market", plan!.Route);
    Assert.Equal(new[] { ExpectedRootClientOrderId }, plan.ClientOrderIds);

    client.ReleaseMarketOrder.TrySetResult(true);
    await store.Ordered.Task.WaitAsync(TimeSpan.FromSeconds(5));

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }

  // Scenario E: recovery ownership is lost mid-operation. The stale recovery
  // worker must stop without advancing confirmations, releasing the record,
  // or deleting the group plan - everything is left for a successor.
  [Fact]
  public async Task BrokerRecoveryOwnershipLossPreservesPlanAndProgress()
  {
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(10));
    var store = new FakeAutoTradeStore(CandidateJson())
    {
      // Frozen clock: recovery can never reach quorum during the test, so it
      // is guaranteed to still be inside the operation when ownership drops.
      AutoAdvanceAbsenceClock = false,
    };
    var client = new FakeTradingClient
    {
      LoseMarketResponseCall = 1,
      // The accepted position stays invisible: every snapshot is empty.
      HideAcceptedMarketPositionsForReconcileCalls = 1_000,
    };
    var engine = new AutoTradeEngine(Options(), store, () => Now, _ => { })
    {
      HeartbeatDelay = (_, token) => Task.Delay(10, token),
      RecoveryDelay = (_, token) => Task.Delay(10, token),
    };
    await engine.ObserveSpotAsync(
      new SpotPrice("XAU", 4000.0m, 4000.2m, Now.ToUnixTimeSeconds()),
      cts.Token
    );

    var run = engine.RunSessionAsync(client, Symbol, cts.Token);
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("broker_recovery_confirmation_recorded")
    );

    // Ownership drops while recovery waits between snapshots.
    store.LeaseRenewalBlocked = true;
    await WaitUntilAsync(() =>
      store.MetricsSnapshot().Contains("broker_recovery_ownership_lost")
    );

    // Nothing was surrendered: the plan and the durable progress survive for
    // the successor, and the record never became retryable or terminal.
    Assert.NotNull(GroupPlanOf(store));
    Assert.Equal(1, store.AbsenceProgressFor(CandidateId)?.Confirmations);
    Assert.DoesNotContain("candidate_retry_waiting", store.MetricsSnapshot());
    Assert.DoesNotContain(
      $"{CandidateExecutionStates.BrokerReconciling}"
        + $"->{CandidateExecutionStates.RetryableError}",
      store.Transitions
    );
    Assert.False(store.Ordered.Task.IsCompleted);
    Assert.Equal(
      CandidateExecutionStates.BrokerReconciling,
      store.CandidateState(CandidateId)
    );
    // The one real submission stays the only one.
    Assert.Single(client.Orders);

    cts.Cancel();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => run);
  }
}
