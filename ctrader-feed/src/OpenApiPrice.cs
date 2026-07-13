namespace ApexVoid.CTraderFeed;

public static class OpenApiPrice
{
  public const decimal Scale = 100_000m;

  public static decimal Decode(decimal rawPrice) => rawPrice / Scale;
}
