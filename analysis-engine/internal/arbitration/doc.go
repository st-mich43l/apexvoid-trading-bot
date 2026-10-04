// Package arbitration resolves conflict between live opportunity.Candidates
// for the same symbol — a distinct concern from internal/confluence, which
// combines independent evidence from candidates that AGREE into one
// stronger score. Arbitrate does the opposite job: when candidates
// DISAGREE (opposite Direction), it decides whether one is decisively
// stronger or the symbol should hold. Putting this in internal/confluence
// would contradict that package's own doc comment ("it never decides
// whether the result is tradeable") and repeat the exact "one mechanism
// wearing different labels" mistake. The ownership decision is recorded
// in docs/adr/004.
//
// Arbitrate is pure and side-effect-free: given one symbol's live
// candidates, it returns a Decision per candidate. It never reads a live
// quote, spread, or account fact (opportunity.Candidate's own doc comment
// already establishes that boundary; this package inherits it by
// construction, since Decision is computed only from Candidate/
// StrategyQuality). Deciding WHEN to act on a Decision (execution-time
// freshness/spot-distance) remains algo-bot's job.
package arbitration
