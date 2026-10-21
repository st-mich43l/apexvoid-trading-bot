# Q3: the quality and acceptance decision map

Question: why does the engine publish one Key Level, iFVG, CRT, Fade Scalp or Trendline opportunity and not another, and does the quality number change anything
after publication? This is the evidence map: each decision, where it is made, whether it is mandatory (validity), a quality gate (acceptance) or informational.

`Market data -> structure -> strategy detector -> confirmation -> geometry -> quality -> acceptance -> Kafka -> Algo Bot`

## 1. Technical validity (mandatory: a failed fact rejects, whatever the score)

| Fact | Where | Applies to |
|---|---|---|
| Closed candles only, chronological, no look-ahead | `engine` feeds closed bars; every detector reads `bars[:e+1]`; prefix-invariance tests per strategy | all |
| Structure exists before it can be reacted to | `Instance.TouchableFrom` + `LegacyDetector.ReactionFrom/ZoneReaction` (zones), `Trendline.AnchorBConfirmedIndex` (lines), gap/inversion order in `ifvg/detect.go`, sweep->reclaim->MSS order in `crt/detect.go` | zones, trendline, iFVG, CRT |
| Level and entry valid for the price | `LegacyDetector.Finish`: `LevelValid`, `EntryValid` run BEFORE the confluence score (`strategyutil/legacy_detect.go:550`) | every detector-contract strategy |
| Strategy formation | per strategy: Key Level role/touches; iFVG gap + inversion + retest + rejection; CRT anchor + sweep + reclaim + shift; Fade equal pool + graded sweep + reaction + failed continuation; Trendline confirmed anchors + health + reclaim interaction | each |
| Invalidation | technical, per strategy (beyond the defended low/sweep extreme/gap/line); never tightened by the envelope | each |
| Opposing target room | Fade: nearest objective with `minimum_reward_risk`; iFVG/CRT/Trendline: nearest credible objective with room; Key Level: `minimum_target_distance_atr`; all: F-7 barrier book in Algo Bot | each |
| `Candidate.Validate()` | `opportunity/candidate.go:333` (finite, ordered geometry, quality in [0,1]) | all |

`TestMaximalEvidenceCannotRescueAnInvalidSetup` pins the order: the maximum evidence cannot make an invalid level or entry valid.

## 2. The one quality gate: the confluence floor

`Finish` returns nil when `SelectedStars < ConfluenceFloor` (2) (`legacy_detect.go:574`); iFVG, CRT and Break & Retest apply the same floor to their own factors, Confluence Zone to its independent-fact count.
It runs after validity, so it can only refuse a valid setup. It is the ONLY place a quality number admits or rejects an opportunity. What the stars are
(`confluence/score.go`): v1 factor points (HTF 4, touches <= 3, wick 3, displacement 3, session 2, structural 3, fib 2.5, CHoCH 2 of 20.5 -> 2 stars at 8, 3 at 12), or the zone score for scored zones.

## 3. Quality that selects among valid candidates inside the engine

When several valid candidates exist on one bar, a strategy publishes the one with more stars: Key Level (`keylevel.go:289`), the technique publishers (`technique_decision.go`, stars then
nearest entry), Fade Scalp (stars then nearest, `fadescalp.go:280`), Confluence Zone. This is operative but it only orders valid siblings of one strategy.

## 4. What the published numbers do after Kafka

| Number | Downstream effect | Evidence |
|---|---|---|
| `quality.overall` | none: `quality_ranking: false` (`config/analysis.yml:699`, `config/auto-algo.yml:43`); Go arbitration `IgnoreQuality`; Python `_neutral_rank` (`arbitration.py:151`); `select_primary` is status reporting only (`multi_match.py:45`) | `tests/test_arbitration_neutral_ranking.py`, `tests/test_quality_is_informational.py` |
| stars / `min_confluence` | `policy_confluence_below_minimum` is a preference note: the plan is still allowed (`execution_policy.py:1395`, `preference_allow`) | `test_a_confluence_below_the_policy_minimum_is_a_preference_note_not_a_rejection` |
| tier A/B/C | none on volume: `risk_multiplier_for_tier` ignores the tier (`execution_policy.py:1497`) | `test_the_volume_multiplier_does_not_depend_on_the_tier` |
| components | published as measurements; nothing reads them | - |

Conclusion: after the Go floor, quality is informational. Acceptance by quality = the floor, and nothing else.

## 5. Per-strategy: what makes one opportunity of the strategy rank above another (and whether it is evidence)

| Strategy | Mandatory facts | Counted toward stars | Duplicate / constant credit found and fixed in Q3 |
|---|---|---|---|
| Key Level | role/kind, touches, reaction pattern, target room | HTF alignment, level touches, reaction pattern (exactly one of wick/engulf/structural), fib, CHoCH, session sweep | `session_context` credited the reaction's own sweep a second time (64 of 4,174); `rejection_choch` named the break twice and the wick not at all; constant `reaction: 1` removed |
| iFVG | gap A/B/C, inversion after C, fresh retest, rejection, room | HTF, retest touches, rejection wick, inversion displacement, structure, session sweep | session sweep on the retest candle (= the rejection wick) no longer counts |
| CRT | anchor, sweep, reclaim, shift, room | HTF, sweep wick, shift (displacement not a second credit) | none left (Q2) |
| Fade Scalp | equal pool, graded sweep, reaction, no continuation, room | HTF, pool touches, sweep wick, reaction displacement | the confirmation's CHoCH named twice (as Key Level) |
| Trendline | confirmed anchors, health, reclaim, target | HTF, line touches, pattern, session sweep | as Key Level |
| Supply/Demand/OB/FVG (resting observations) | zone chain, proximity | `quality.overall` = freshness only now | fabricated `Strength = 1` / `Relevance = Immediate` (`engine/technique_zones.go:91`) were published as components |

## 6. Findings classification

* **Confirmed technical defects (fixed, regression tests):** one event credited twice (session sweep vs the reaction's own pierce; CHoCH as structural + choch); unconditional `reaction: 1`; fabricated strength/relevance components on resting zones; engine fallback inference from evidence wording (Q2) ; Fade Scalp had no account of why it refused a setup.
* **Unsupported quality assumptions (no longer treated as proof, nothing re-weighted):** stars and `quality.overall` as a ranking of outcomes; HTF alignment as a positive factor; the 3-star bin as a premium.
* **Potential predictive improvements:** none supported by the evidence in [q3-quality-validation.md](q3-quality-validation.md).
* **Insufficient evidence:** CRT, Trendline, iFVG within-strategy, Flip Zone, Break & Retest, XAU for most strategies.
