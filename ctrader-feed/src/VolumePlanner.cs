namespace ApexVoid.CTraderFeed;

public static class VolumePlanner
{
  public static decimal LotsForBalance(decimal balance) => balance switch
  {
    >= 5_000m => 0.30m,
    >= 2_000m => 0.20m,
    >= 1_000m => 0.12m,
    _ => 0m,
  };

  public static long VolumeForLots(decimal lots, SymbolInfo symbol)
  {
    if (lots <= 0 || symbol.LotSize <= 0 || symbol.StepVolume <= 0)
    {
      return 0;
    }
    var raw = decimal.ToInt64(decimal.Floor(lots * symbol.LotSize));
    var stepped = raw / symbol.StepVolume * symbol.StepVolume;
    if (stepped < symbol.MinVolume || stepped > symbol.MaxVolume)
    {
      return 0;
    }
    return stepped;
  }

  public static IReadOnlyList<long> SplitFive(long volume, SymbolInfo symbol)
  {
    if (volume <= 0 || symbol.StepVolume <= 0)
    {
      throw new InvalidOperationException("Symbol volume step is unavailable");
    }
    var baseSlice = volume / 5 / symbol.StepVolume * symbol.StepVolume;
    if (baseSlice < symbol.MinVolume)
    {
      throw new InvalidOperationException(
        "Configured volume is too small for five broker-valid partial closes"
      );
    }
    var slices = Enumerable.Repeat(baseSlice, 4).ToList();
    slices.Add(volume - slices.Sum());
    if (slices[^1] < symbol.MinVolume || slices[^1] % symbol.StepVolume != 0)
    {
      throw new InvalidOperationException("Final partial-close volume is invalid");
    }
    return slices;
  }
}
