namespace ApexVoid.CTraderFeed;

/// <summary>
/// Account-level exposure and reconciliation coordinator.
/// Instrument contexts own pip/digits/limits; this owns shared account risk.
/// </summary>
public sealed class AccountRiskCoordinator
{
  private readonly object _gate = new();
  private decimal _openExposureNotional;
  private int _openPositionCount;

  public decimal OpenExposureNotional
  {
    get
    {
      lock (_gate)
      {
        return _openExposureNotional;
      }
    }
  }

  public int OpenPositionCount
  {
    get
    {
      lock (_gate)
      {
        return _openPositionCount;
      }
    }
  }

  public void ObserveAccountPositions(IReadOnlyList<TradingPosition> positions)
  {
    lock (_gate)
    {
      _openPositionCount = positions.Count;
      // Notional is account-global; instrument pip math stays in InstrumentRiskContext.
      _openExposureNotional = positions.Sum(position => Math.Abs(position.Volume));
    }
  }

  public IReadOnlyList<TradingPosition> PartitionByInstrument(
    IReadOnlyList<TradingPosition> positions,
    InstrumentRuntime runtime
  )
  {
    var symbolId = runtime.Symbol?.SymbolId;
    if (symbolId is null)
    {
      return [];
    }
    return positions.Where(position => position.SymbolId == symbolId.Value).ToArray();
  }

  public IReadOnlyList<TradingPosition> UnmanagedPositions(
    IReadOnlyList<TradingPosition> positions,
    InstrumentRuntimeRegistry registry
  )
  {
    return positions
      .Where(position => !registry.TryGetBySymbolId(position.SymbolId, out _))
      .ToArray();
  }
}

/// <summary>Instrument-scoped risk units (pip, digits, contract, limits).</summary>
public sealed record InstrumentRiskContext(
  string InstrumentId,
  decimal PipSize,
  int PriceDigits,
  decimal ContractUnitsPerLot,
  int MaxPositions
);
