namespace ApexVoid.CTraderFeed;

// Raised when the executor can no longer prove it owns the candidate lease.
// A stale executor must stop rather than mutate a successor's candidate.
public sealed class CandidateLeaseLostException : Exception
{
  public CandidateLeaseLostException(string candidateId)
    : base($"candidate lease lost for {candidateId}")
  {
    CandidateId = candidateId;
  }

  public string CandidateId { get; }
}

// Continuous compare-token lease renewal for the whole duration of a
// candidate's execution. Long broker operations (market placement, each
// zone-fill leg, stop amendment, rollback, reconciliation) can individually
// outlive a fixed lease, so ownership is refreshed well below one third of
// the lease window and every broker side effect is gated on the last known
// ownership state.
public sealed class CandidateLeaseHeartbeat : IAsyncDisposable
{
  private readonly IAutoTradeStore _store;
  private readonly CandidateExecutionLease _lease;
  private readonly TimeSpan _leaseDuration;
  private readonly TimeSpan _renewalInterval;
  private readonly Func<TimeSpan, CancellationToken, Task> _delay;
  private readonly Func<string, CancellationToken, Task> _metric;
  private readonly Action<string> _log;
  private readonly SemaphoreSlim _renewGate = new(1, 1);
  private readonly CancellationTokenSource _stopped;
  private readonly CancellationTokenSource _ownership = new();
  private Task _loop = Task.CompletedTask;
  private volatile bool _ownershipLost;
  private int _ownershipLostFlag;
  private long _renewals;
  private bool _disposed;

  private CandidateLeaseHeartbeat(
    IAutoTradeStore store,
    CandidateExecutionLease lease,
    TimeSpan leaseDuration,
    TimeSpan renewalInterval,
    Func<TimeSpan, CancellationToken, Task> delay,
    Func<string, CancellationToken, Task> metric,
    Action<string> log,
    CancellationToken cancellationToken
  )
  {
    _store = store;
    _lease = lease;
    _leaseDuration = leaseDuration;
    _renewalInterval = renewalInterval;
    _delay = delay;
    _metric = metric;
    _log = log;
    _stopped = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
  }

  public CandidateExecutionLease Lease => _lease;

  public bool OwnershipLost => _ownershipLost;

  // Cancelled as soon as ownership is lost so in-flight broker work that is
  // safe to abandon can unwind instead of racing the successor.
  public CancellationToken OwnershipToken => _ownership.Token;

  public long Renewals => Interlocked.Read(ref _renewals);

  public static CandidateLeaseHeartbeat Start(
    IAutoTradeStore store,
    CandidateExecutionLease lease,
    TimeSpan leaseDuration,
    CancellationToken cancellationToken,
    TimeSpan? renewalInterval = null,
    Func<TimeSpan, CancellationToken, Task>? delay = null,
    Func<string, CancellationToken, Task>? metric = null,
    Action<string>? log = null
  )
  {
    // Renew well below a third of the lease so a single delayed scheduling
    // slice cannot expire the lease.
    var floor = TimeSpan.FromMilliseconds(
      Math.Max(50d, leaseDuration.TotalMilliseconds / 4d)
    );
    var interval = renewalInterval ?? floor;
    var ceiling = TimeSpan.FromMilliseconds(leaseDuration.TotalMilliseconds / 3d);
    if (interval > ceiling)
    {
      interval = ceiling;
    }
    var heartbeat = new CandidateLeaseHeartbeat(
      store,
      lease,
      leaseDuration,
      interval,
      delay ?? ((wait, token) => Task.Delay(wait, token)),
      metric ?? ((_, _) => Task.CompletedTask),
      log ?? (_ => { }),
      cancellationToken
    );
    heartbeat._loop = heartbeat.RunAsync();
    return heartbeat;
  }

  // Renews immediately and reports whether the caller still owns the lease.
  // Call this directly before every broker side effect.
  public async Task<bool> EnsureOwnershipAsync(CancellationToken cancellationToken)
  {
    if (_ownershipLost)
    {
      return false;
    }
    return await RenewOnceAsync(cancellationToken);
  }

  public async Task ThrowIfOwnershipLostAsync(CancellationToken cancellationToken)
  {
    if (!await EnsureOwnershipAsync(cancellationToken))
    {
      throw new CandidateLeaseLostException(_lease.CandidateId);
    }
  }

  private async Task RunAsync()
  {
    try
    {
      while (!_stopped.IsCancellationRequested && !_ownershipLost)
      {
        await _delay(_renewalInterval, _stopped.Token);
        if (_stopped.IsCancellationRequested)
        {
          return;
        }
        await RenewOnceAsync(_stopped.Token);
      }
    }
    catch (OperationCanceledException)
    {
      // Normal shutdown once execution finishes.
    }
  }

  private async Task<bool> RenewOnceAsync(CancellationToken cancellationToken)
  {
    if (_ownershipLost)
    {
      return false;
    }
    // Serialised so a scheduled renewal and a pre-broker ownership check never
    // overlap on the same token.
    await _renewGate.WaitAsync(cancellationToken);
    try
    {
      if (_ownershipLost)
      {
        return false;
      }
      bool renewed;
      try
      {
        renewed = await _store.RenewCandidateLeaseAsync(
          _lease.CandidateId,
          _lease.StreamEventId,
          _lease.Token,
          _leaseDuration,
          cancellationToken
        );
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        // A renewal error is never silently ignored: ownership becomes
        // unprovable and every later broker side effect must be blocked.
        _log(
          $"auto-trade lease heartbeat error for {_lease.CandidateId}: "
          + exception.Message
        );
        await MarkLostAsync("executor_lease_heartbeat_failed", cancellationToken);
        return false;
      }
      if (!renewed)
      {
        await MarkLostAsync("executor_lease_heartbeat_failed", cancellationToken);
        return false;
      }
      Interlocked.Increment(ref _renewals);
      await SafeMetricAsync("executor_lease_heartbeat_success", cancellationToken);
      return true;
    }
    finally
    {
      _renewGate.Release();
    }
  }

  private async Task MarkLostAsync(string metric, CancellationToken cancellationToken)
  {
    // Ownership cancellation must not be blocked by metrics or by a session
    // token that is already cancelled. Mark once, cancel first, then best-
    // effort telemetry.
    if (Interlocked.Exchange(ref _ownershipLostFlag, 1) == 1)
    {
      return;
    }
    _ownershipLost = true;
    try
    {
      await _ownership.CancelAsync();
    }
    catch (ObjectDisposedException)
    {
    }
    await SafeMetricAsync(metric, CancellationToken.None);
  }

  private async Task SafeMetricAsync(string metric, CancellationToken cancellationToken)
  {
    try
    {
      await _metric(metric, cancellationToken);
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      _log($"auto-trade heartbeat metric {metric} failed: {exception.Message}");
    }
  }

  public async ValueTask DisposeAsync()
  {
    if (_disposed)
    {
      return;
    }
    _disposed = true;
    await _stopped.CancelAsync();
    try
    {
      await _loop;
    }
    catch (OperationCanceledException)
    {
    }
    _stopped.Dispose();
    _ownership.Dispose();
    _renewGate.Dispose();
  }
}
