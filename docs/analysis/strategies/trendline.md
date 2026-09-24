# Trendline Strategy V2

`trendline` consumes immutable causal M5 anchors and the canonical interaction
classifier. A line needs the configured validation touches, a valid approach,
and a reclaimed support/resistance close. Entry is the interaction band,
invalidation is a close-violation buffer, and target is strategy-owned R.
Identity is the anchor pair, never evaluation time. Broken/exhausted lines and
testing without reclaim reject. Enabled for Go shadow publication; it does not
alter Python live-trading policy.
