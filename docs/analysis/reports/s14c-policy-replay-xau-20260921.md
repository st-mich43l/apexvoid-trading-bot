# Go vs Python policy replay: XAU M5 supply / demand

**Verdict: `unresolved_differences`** (conservative; this report approves nothing).

## Inputs
- Capture `xau-production-capture-20260921.json` sha256 `a7663f03efe8f564…`, bars {'H1': 300, 'M15': 600, 'M5': 1500}, H4 derived from H1: False.
- Go: 185 confirmed reviewed-scope cases (left out: none).
- Python: 177 observations from `../contracts/analysis/replay/python-observations-xau-20260921.jsonl`, stride 1.
- Match tolerance: 300 s on the confirmation bar plus positive zone overlap.

## Result
- Matched **22**, Python only **155**, Go only **163**.
- Go adaptation: 173/185 adaptable; rejections {'higher_timeframe_bias_unavailable': 12}.
- Many-to-many coverage within ±900 s (an engine re-confirming the same zone on consecutive bars is not penalised): Go cases with an overlapping Python confirmation nearby 39/185; Python observations with an overlapping Go confirmation nearby 53/177.

### Field agreement on matched setups
| policy input | agree | total | rate |
| --- | ---: | ---: | ---: |
| confirmation_time_exact | 11 | 22 | 50.0% |
| confirmation_kind_same_family | 9 | 22 | 40.9% |
| entry_zone_iou_ge_0_5 | 7 | 22 | 31.8% |
| invalidation_vs_unbuffered_python_edge_within_0_5_atr | 1 | 22 | 4.5% |
| atr_within_1pct | 3 | 22 | 13.6% |
| htf_bias_agree | 13 | 22 | 59.1% |
| tier_agrees | 3 | 22 | 13.6% |
| opposing_structure_agree | 20 | 22 | 90.9% |

- Higher-timeframe relations: {'conflict': 9, 'agree': 13}.
- Python confirmation kinds: {'engulfing': 5, 'wick_rejection': 9, 'sweep_reclaim': 5, 'rejection_choch': 1, 'strong_reclaim': 2}.
- Confluence: Python values {2: 19, 3: 3} vs Go evidence counts {4: 22}; tiers Python {'B': 19, 'A': 3} vs Go {'A': 22}.

### Gates
- PASS: `python_replay_is_full_stride`
- OPEN: `every_go_case_adaptable`
- OPEN: `no_python_only`
- OPEN: `no_go_only`
- OPEN: `all_matched_fields_in_tolerance`
- OPEN: `no_htf_unavailability_or_conflict`

## Findings on the five S14C questions

### 1. Confirmation
- Missed by Go (Python-only): **155**; extra in Go (Go-only): **163**.
- Matched confirmation-time delta (seconds → count): {'-300': 11, '0': 11}.
- Go touch→confirmation gap (bars → count): {'0': 169, '1': 16}.
- Python kinds, matched {'engulfing': 5, 'wick_rejection': 9, 'sweep_reclaim': 5, 'rejection_choch': 1, 'strong_reclaim': 2}; unmatched {'engulfing': 28, 'wick_rejection': 49, 'strong_reclaim': 32, 'sweep_reclaim': 41, 'rejection_choch': 5}.

### 2. Confluence, tier, risk multiplier, eligibility
- Go confluence (evidence count) {'4': 173} → tiers {'A': 173}; matched Python confluence {'2': 19, '3': 3} → tiers {'B': 19, 'A': 3}.
- Risk multiplier: Go {'1.0': 173} vs matched Python {'1.0': 22} (reaction sizing ignores tier).
- Session-floor eligibility: Go 173/173 pass; matched Python 22/22 pass; matched setups Go passes but Python would not: **0**.

### 3. Higher-timeframe bias
- Timeframe used by the adapter: {'none': 12, 'H1': 173}; present in Go events: {'none': 12, 'H1': 173}; Go H1-vs-H4 disagreements preserved: 0.

### 4. ATR and geometry
- Go-reported ATR equals the configured `simple` formula recomputed from the capture: 185/185.
- Python's Wilder ATR relative to Go's reported: {'n': 185, 'min': -0.244962, 'median': 0.011635, 'max': 0.30936}.
- Prices on the instrument tick: 0/185; stop pips {'n': 185, 'min': 43.6393, 'median': 112.7071, 'max': 695.1143}; R:R to first target {'n': 185, 'min': 0.1935, 'median': 0.5412, 'max': 3.3004}; whole-pip target rounding error (price) {'n': 185, 'min': 0.000357, 'median': 0.025929, 'max': 0.049929}.

### 5. Opposing structure
- Go carries no opposing-structure fact. The worker's final barrier / target-room checks still recompute Python zones and levels from M15 OHLC (`worker._htf_zones`, `_htf_levels`, `_opposing_barrier_reason`, `structural_target_room`); they are retained. Per-pair verdicts above use that check on identical frames.

### Go-only (163; 0 fall outside the Python replay window)
- `opp_99c406eb6cd071` supply at 1789373400 entry [4316.06, 4323.79] htf -
- `opp_c381c1b0801fc8` supply at 1789377000 entry [4309.61, 4315.95] htf -
- `opp_f087bc21415787` supply at 1789388100 entry [4291.21, 4296.87] htf {'H1': 'SELL'}
- `opp_6a95da9699610d` supply at 1789389300 entry [4284.37, 4294.41] htf {'H1': 'SELL'}
- `opp_382610a4a004f2` supply at 1789389900 entry [4284.56, 4297.62] htf {'H1': 'SELL'}
- `opp_7030d875b71396` supply at 1789389900 entry [4284.37, 4294.41] htf {'H1': 'SELL'}
- `opp_ae3719f2e14f03` supply at 1789392600 entry [4273.55, 4289.56] htf {'H1': 'SELL'}
- `opp_5ac241d39355cc` supply at 1789392900 entry [4273.55, 4289.56] htf {'H1': 'SELL'}
- `opp_85910407d4832f` demand at 1789393200 entry [4253.68, 4272.66] htf {'H1': 'SELL'}
- `opp_7cfa0d39e3ce41` demand at 1789393500 entry [4253.68, 4272.66] htf {'H1': 'SELL'}
- `opp_5eff478cd825ce` supply at 1789393800 entry [4273.55, 4289.56] htf {'H1': 'SELL'}
- `opp_a60ed097aae15d` supply at 1789394100 entry [4273.55, 4289.56] htf {'H1': 'SELL'}
- `opp_1188e83b51100f` supply at 1789394400 entry [4273.55, 4289.56] htf {'H1': 'SELL'}
- `opp_3654d9ebef4903` demand at 1789395000 entry [4253.68, 4272.66] htf {'H1': 'SELL'}
- `opp_6eac4430307294` demand at 1789399500 entry [4264.04, 4282.98] htf {'H1': 'SELL'}
- `opp_f2562a713f92f4` demand at 1789399500 entry [4266.12, 4281.06] htf {'H1': 'SELL'}
- `opp_087ccf5a9783a5` supply at 1789418400 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_d0bf15f64b8e4d` supply at 1789424700 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_c4b7f26bbbd31c` supply at 1789425300 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_37b4c59f896777` supply at 1789425600 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_98f1d85c2fc515` supply at 1789425900 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_528427b2e1d633` supply at 1789426500 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_7b5216c167d7ed` supply at 1789426800 entry [4295.97, 4306.72] htf {'H1': 'SELL'}
- `opp_754b76ecdc98fe` demand at 1789433100 entry [4284.88, 4292.3] htf {'H1': 'SELL'}
- `opp_fa318abb386c6b` supply at 1789433700 entry [4293.17, 4298.24] htf {'H1': 'SELL'}
- … 138 more in the JSON report

### Python-only (155)
- `py_5b8036b13eb55e69` supply at 1789412400 entry [4306.4, 4311.4] kind engulfing
- `py_05f504a841baff22` demand at 1789416000 entry [4281.74, 4286.74] kind wick_rejection
- `py_7914efc3d2262ca5` demand at 1789416000 entry [4281.74, 4286.74] kind wick_rejection
- `py_a64843a89556b5cf` demand at 1789431000 entry [4281.74, 4286.74] kind wick_rejection
- `py_b35ea82786f155d2` demand at 1789431000 entry [4286.95, 4291.95] kind wick_rejection
- `py_179a5c35a6bb28e8` supply at 1789438200 entry [4302.92, 4307.89] kind strong_reclaim
- `py_d77c52be4cfd8eee` supply at 1789438200 entry [4302.92, 4307.89] kind strong_reclaim
- `py_74b3fec4207af072` demand at 1789450500 entry [4293.55, 4298.55] kind wick_rejection
- `py_0e5a2525acb4d801` demand at 1789450800 entry [4293.55, 4298.55] kind sweep_reclaim
- `py_a5cd18ab9dc2efad` demand at 1789450800 entry [4293.55, 4298.55] kind sweep_reclaim
- `py_e89f2a5503a13933` demand at 1789450800 entry [4293.55, 4298.55] kind sweep_reclaim
- `py_19e3a5584fc5ba87` demand at 1789452600 entry [4284.72, 4289.72] kind wick_rejection
- `py_4d9c44381e4ce395` demand at 1789452600 entry [4284.72, 4289.72] kind wick_rejection
- `py_10f0df508d8add43` demand at 1789456500 entry [4284.72, 4289.72] kind engulfing
- `py_e400b6458bada1dd` supply at 1789468500 entry [4276.28, 4281.28] kind wick_rejection
- `py_290b117b8313d46c` supply at 1789469700 entry [4276.28, 4281.28] kind wick_rejection
- `py_3ea4806b9b842f64` supply at 1789469700 entry [4281.0, 4286.0] kind wick_rejection
- `py_3fa274bf7f93084d` supply at 1789469700 entry [4281.0, 4286.0] kind wick_rejection
- `py_423271eae9e162c9` supply at 1789470900 entry [4281.0, 4286.0] kind wick_rejection
- `py_e1bb994170b3b476` supply at 1789471500 entry [4281.0, 4286.0] kind engulfing
- `py_6fbf42ac9431fc21` supply at 1789472100 entry [4281.0, 4286.0] kind engulfing
- `py_931fc3bf42fef3af` supply at 1789472100 entry [4281.0, 4286.0] kind engulfing
- `py_7d03d80ee4041e54` supply at 1789473000 entry [4281.0, 4286.0] kind wick_rejection
- `py_ab4f6dd8efa43a97` supply at 1789473000 entry [4281.0, 4286.0] kind wick_rejection
- `py_bad2c2fb838ec393` supply at 1789473000 entry [4281.0, 4286.0] kind wick_rejection

## Limits
- One real capture of one instrument over the captured window; not a performance or live-equivalence claim.
- No H4: the production feed delivers no H4 (config/trading-bot.yml), so Go's higher-timeframe context is H1 only and the adapter's H4 fallback is unreachable live.
- Python replay covers only the "Supply Demand" technique detector on frames visible at each M5 close (one best result per bar); scanner-level actionability gates and the market-map layer are not replayed.
- Opposing-structure verdicts use the worker's Python barrier function on identical frames for both sides; Go supplies no opposing-structure fact.
- The verdict is conservative: 'pass' needs every gate; anything else needs an owner disposition. This report approves nothing.
