# Range Edge Strategy V2

`range_edge` requires canonical M5 internal range state plus repeated wick
rejections at one lookback boundary. It buys the lower edge and sells the upper
edge, invalidates beyond the boundary and targets the opposite edge. Identity
is anchored to the range's first bar and chosen side. A trending context,
insufficient rejection count or a close away from both edges rejects. Enabled
for Go shadow publication; it does not alter Python live-trading policy.
