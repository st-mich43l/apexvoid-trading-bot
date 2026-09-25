namespace ApexVoid.CTraderFeed;

// The V6 zone-fill ladder builder (formerly ZoneFillPlanner.Qualifies/
// .Build in this file) was removed entirely. This record survives only
// because ProcessSingleLimitInitialAsync's single-limit route still
// reuses it purely as a comment-formatting DTO (BuildZoneComment takes
// one leg regardless of route).
public sealed record ZoneFillLegPlan(
  int Leg,
  decimal LimitPrice,
  long Volume,
  TargetVolumePlan TargetPlan
);
