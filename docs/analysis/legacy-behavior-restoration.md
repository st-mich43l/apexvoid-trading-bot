# Go legacy-behavior restoration report

## Production boundary

The automatic path remains Market Feed → Go Analysis Engine → Kafka →
Algo Bot execution policy. Python detector/scanner code is not called by the
runtime and no authority, shadow, fallback, or old/new selector was added.

The parity zone chain is Go code (`internal/legacyzone`). Its validated
technique instances now replace the supply/demand, order-block, FVG and iFVG
qualification population inside the one canonical `zone.ZoneState`. V2 still
owns lifecycle, relevance, non-technique zone kinds, deterministic opportunity
identity, invalidation, expiry and publication.

## Restored behavior

| Strategy/domain | Classification | Live behavior |
|---|---|---|
| Supply/demand, OB, FVG, iFVG geometry | PROVEN_EXISTING_PARITY | Frozen-Python zone construction, mitigation and technique validation feed the canonical Go context. |
| CRT discovery | PROVEN_EXISTING_PARITY | Go parity implementation uses H1 range, M5 sweep/reclaim, validated entry slice and shared reaction timestamps. |
| Snap Back | EXACT_PORT (decision flow) | Direction/location, strict reversal PD, preferred structural zone with key-level fallback, configured extension source, A/B grab and shared structural reaction. |
| Range Edge | EXACT_PORT (decision flow) | Two-sided barrier history, touch episodes, wick count, accepted closes, Grade-A exception, dynamic touch lookback, EQ and opposite-edge targets. |
| Momentum Ride | EXACT_PORT (decision flow) | Chop rejection, loose continuation PD, structural body break, optional legacy VA gate, broken-swing identity and zone/level fallback. Continuity remains quality-only. |
| Fade Scalp | EXACT_PORT (decision flow) | Independent equal-high/equal-low A/B sweep reversal, strict reversal PD, Grade-A chop-edge rule and shared structural reaction. |
| Protected structure, canonical MTF context, lifecycle and Kafka | INTENTIONAL_GO_IMPROVEMENT | Causal Go state and deterministic lifecycle remain authoritative. |
| Trendline V2 and Key Level | INTENTIONAL_GO_IMPROVEMENT | Existing causal ports remain unchanged. |

## Golden parity

| Fixture | Result |
|---|---:|
| Shared structural reaction | 2,500 / 2,500 cases identical; 951 confirmations |
| Full zone chain | 120 / 120 windows identical; 24 full windows |
| Technique instances | 138 / 138 identical |
| CRT | 360 / 360 windows; 23 / 23 instances identical |
| Momentum state | 160 / 160 cases identical |

The repository contains no EURUSD or USDJPY full multi-timeframe replay
capture. Cross-instrument reaction parity includes both XAU and EURUSD; the
production replay below is therefore XAU only rather than a fabricated FX
comparison.

## Three-way production replay

The committed 2,400-event XAU capture dispatches M5, M15 and H1 causally.

| Strategy | Frozen Python fact coverage | #721 simplified Go | Restored Go |
|---|---|---:|---:|
| Snap Back | reaction/zone/grab primitives golden-tested | 10, unconfirmed | 24, all confirmed |
| Range Edge | shared reaction golden-tested | 30, unconfirmed | 125, all confirmed |
| Momentum Ride | momentum primitive golden-tested | 34 | 14 structural-break candidates |
| Fade Scalp | equal-level/liquidity/reaction primitives | absent | 22, all confirmed |
| CRT | exact decision golden | simplified candidate path | 7, all confirmed |

Final replay total: **2,633** deterministic opportunities. Technique-family
counts are demand 176, supply 139, order block 62, FVG 92, iFVG 136 and CRT 7.
The committed envelope SHA-256 is recorded in
`contracts/analysis/replay/go-replay-xau-20260921.meta.json`.

Counts are semantic restoration evidence, not PnL tuning. Thresholds came
from the frozen behavior/config or existing canonical Go settings; no replay
trade was used to optimize profitability.

## Runtime observability

Each successful lifecycle publication logs strategy, direction, structural
identity, evidence codes (including PD and liquidity grade), reaction pattern
and timestamps, HTF relationship, session-context presence, confluence stars,
and invalidation price/reason. The Kafka opportunity retains the same typed
facts for downstream analysis.
