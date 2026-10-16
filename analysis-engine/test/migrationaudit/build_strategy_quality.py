#!/usr/bin/env python3
"""Assemble python-go-strategy-diff.json and python-go-quality-diff.json.

The strategy facts below were measured in this audit (oracle goldens, parity-test logs, historical worktrees, production
read-only exports); each entry names its evidence. The quality distributions come from the all-strategy replay records
(analysis-engine/test/aequality AEQ_DUMP) and the production figures from a read-only export of auto_trade_results
(trade_stream='algo_auto').

Usage: build_strategy_quality.py --aeq-dist quality_distribution.json --prod production_by_strategy.json --out-dir docs/architecture
"""
import argparse, json, os

STRATEGIES = [
  dict(id="key_level", version="v3", python="detectors.key_level_reaction (a1c77584)", verdict="EXACT_PARITY",
       contract="detector_contract: profit_week (config/analysis.yml:170) selects the pre-21-Sep structure (LegacyFrame.SwingLevels/ContractZones)",
       evidence="test/keylevelparity vs key-level-oracle.json (a1c77584): 3,310 Python decisions over EURUSD 598, GBPUSD 660, USDJPY 691, XAU 638, XAU_LATER 723; 0 mismatches on 6x1,351 bars (negatives included)",
       vacuous=["GBPJPY: the oracle holds 0 decisions, so GBPJPY parity is not demonstrated by it"],
       defects=["F-1 (latent): SwingLevels ignore the instrument's minimum_key_touches (GBPJPY 3): 505 of 1,284 GBPJPY swing levels have 2 touches; the frozen engine kept none. The strategy's own touches gate (applyKeyLevelOverrides) masks it for decisions; the level book, the flip zones built from it and the opposing-zone check still differ on GBPJPY"],
       negative_bars="1,351 bars per capture; Go silent wherever Python was silent (0 extra decisions)"),
  dict(id="confluence_zone", version="v3", python="technique_detectors.confluence_zone_reaction + confluence_zone.* (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="v3 'distinct-zone overlap' (min independent fact groups) with measured confirmation, honest invalidation and target",
       evidence="test/techniqueparity proves the frozen publisher through strategyutil.TechniqueExcludingConfluenceCoverage on 871 decisions (1 listed divergence = a Python defect); test/czreplay certifies v3 against the frozen baselines (Model A legacy intersection, Model B Python-parity union)",
       vacuous=["the parity test exercises the helper path, not the live v3 strategy"],
       defects=[], python_bugs=["_zone_grabs_for called without pip_size: hard-coded 0.1-pip sweep tolerance on every instrument (USDJPY bar 1790888700)"],
       negative_bars="technique oracle bars are the bars where any publisher fired (EURUSD 506, GBPJPY 498, GBPUSD 521, USDJPY 644, XAU 379); silence elsewhere is asserted by the test"),
  dict(id="supply", version="v3", python="technique_detectors.supply_demand_technique_reaction (1c9f323)", verdict="PARITY_WITH_KNOWN_DIFFERENCES",
       contract="confirmed path = strategyutil.ConfirmedTechnique (legacy 150-bar frame); resting path reads Context.Zones",
       evidence="technique oracle 1c9f323: supply_demand 1,164 decisions on 5 symbols, bar-by-bar, 0 unexplained mismatches",
       vacuous=["the resting path has no Python predecessor: the adapter rejects it (requires_reaction)"],
       defects=[], differences=["v3 refuses zones touched more than twice (7e399bd2)", "strategy independence: no longer silenced by a confluence band (8e5a20a2): +627 supply_demand publications that the frozen publisher suppressed"],
       negative_bars="as confluence_zone"),
  dict(id="demand", version="v3", python="technique_detectors.supply_demand_technique_reaction (1c9f323)", verdict="PARITY_WITH_KNOWN_DIFFERENCES",
       contract="as supply", evidence="as supply (same publisher, BUY side)", vacuous=[], defects=[], differences=["as supply"], negative_bars="as supply"),
  dict(id="order_block", version="v2", python="technique_detectors.order_block_technique_reaction (1c9f323)", verdict="PARITY_WITH_KNOWN_DIFFERENCES",
       contract="as supply", evidence="technique oracle: 26 decisions (EURUSD 9, GBPJPY 3, GBPUSD 7, USDJPY 3, XAU 4): a thin positive set",
       vacuous=["26 positives over 5 symbols"], defects=[], differences=["independence: +273 publications previously suppressed"], negative_bars="as supply"),
  dict(id="fvg", version="v2", python="technique_detectors.fvg_technique_reaction (1c9f323)", verdict="PARITY_WITH_KNOWN_DIFFERENCES",
       contract="as supply", evidence="technique oracle: 161 decisions", vacuous=[], defects=[], differences=["independence: +166 publications previously suppressed"], negative_bars="as supply"),
  dict(id="ifvg", version="v3", python="technique_detectors.ifvg_technique_reaction (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="iFVG v3: an independent Go contract for the inverse fair-value gap (0c5d154d)",
       evidence="technique oracle proves the frozen publisher through the helper path (641 decisions); test/ifvgreplay pins the v2 defects and certifies v3 on real captures",
       vacuous=["the parity test does not run the v3 strategy"], defects=[], negative_bars="as supply"),
  dict(id="crt", version="v3", python="technique_detectors.crt_technique_reaction (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="CRT v3: timestamp-correct H1 range / M5 reclaim (93dd105f)",
       evidence="techniquezone-crt-parity.json proves the port of the frozen publisher; test/crtreplay certifies v3 (invariants, no future dependence, golden); 94 frozen decisions via the helper",
       vacuous=["GBPUSD has 2 frozen decisions"],
       defects=[], python_bugs=["frozen CRT instances carry an H1 position that callers index into M5/M15 bars: independently reproduced by this audit, 112 of 114 oracle CRT instances have origin_ts != the bar at origin_index"],
       negative_bars="as supply"),
  dict(id="flip_zone", version="v3", python="detectors.flip_demand_zone_reaction / flip_supply_zone_reaction (1c9f323)", verdict="EXACT_PARITY",
       contract="restored to the frozen detectors' decisions on 8 Oct (c9663738)",
       evidence="test/legacyparity vs legacy-strategies-oracle.json: 91 Python flip decisions (EURUSD 17, GBPJPY 13, GBPUSD 15, USDJPY 10, XAU 36), asserted bar by bar, 0 mismatches",
       vacuous=[], defects=[], negative_bars="77-111 captured bars per symbol in the golden"),
  dict(id="session_level", version="v4", python="detectors.session_level_reaction (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="v4: complete periods, two bounded reaction models, honest geometry",
       evidence="test/legacyparity measures but does not assert: 368 divergences against 316 oracle decisions (oracle-only 34 of the first 40 shown); test/slreplay certifies v4",
       vacuous=["parity is not asserted by design"], defects=["SessionContext: true is a constant in the v4 scorer (see quality diff)"], negative_bars="-"),
  dict(id="trendline", version="v4", python="detectors.trendline_reaction (_v1/_v2) (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="v4 publishes only with a real opposing liquidity target >= minimum_target_distance_atr",
       evidence="test/legacyparity: 8 oracle decisions (GBPJPY 2, GBPUSD 2, XAU 4), 1 measured divergence; 0 production trades",
       vacuous=["8 positives over 5 symbols"], defects=[], negative_bars="-"),
  dict(id="range_edge", version="v3", python="detectors.range_edge_scalp (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="v3 refuses to fade an edge the market accepted through",
       evidence="frozen v2 decisions kept as testdata/range-edge-v2-baseline.json and replayed by test/rereplay; the Python oracle (detector-parity-oracle.json: 319 decisions on GBPUSD, USDJPY, XAU) is no longer asserted",
       vacuous=[], defects=[], negative_bars="-"),
  dict(id="break_retest", version="v3", python="detectors.break_retest (1c9f323)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="v3 measured, fresh-retest contract (39240f81)",
       evidence="oracle: 28 decisions (GBPUSD 9, USDJPY 11, XAU 8) no longer asserted; v2 baseline in test/brreplay",
       vacuous=[], defects=["Quality.Overall is the constant 0.75 (published_overall_quality)"],
       notes="break_retest_enabled was true in the a1c77584 configuration (config/trading-bot.yml:834): the frozen detector was live"),
  dict(id="fade_scalp", version="v2", python="detectors.fade_scalp (1c9f323)", verdict="EXACT_PARITY",
       contract="frozen detector decisions", evidence="test/detectorparity covered=[fade_scalp]: 94 Python decisions (GBPUSD 33, USDJPY 43, XAU 18), 0 mismatches over 3x1,351 bars",
       vacuous=["EURUSD and GBPJPY are not in the golden"], defects=[], negative_bars="1,351 bars on each of 3 symbols"),
  dict(id="liquidity_sweep", version="v3", python="none in the analysis chain (the Python lane had Range Sweep / Snap-Back on the scalp lane)", verdict="INTENTIONAL_RECONSTRUCTION",
       contract="rebuilt on the frame grabs (b4543d20)", evidence="internal/strategy/liquiditysweep unit tests only; no Python oracle exists for this contract",
       vacuous=["nothing to compare against"], defects=["Quality.Overall = reclaim-bar body / ATR; 'reclaim' component is the constant 1"], negative_bars="-"),
  dict(id="impulse_pullback", version="v3", python="app/scalping/strategies.discover_impulse_pullback (a1c77584)", verdict="UNVERIFIED",
       contract="port of the scalp lane; M1-evaluated, M5 structure",
       evidence="F-4: its parity tests were deleted on 14 Oct (9d705a02, collateral of removing Range Sweep). Re-run at 9d705a02^ (code unchanged since): real XAU capture 1,939 cycles, 0 Python positives; 9 synthetic captures 18/18 positives matched (synthetic M1 data). No in-tree test exercises it against Python now",
       vacuous=["zero real-data positives"], defects=["no test file in internal/strategy/impulsepullback; evidence-keyword inference is inert for its static codes"], negative_bars="1,939 real M1 cycles (historical)"),
]

RETIRED = [
  dict(id="box_breakout", verdict="RETIRED", note="purged 15 Oct (00c28055); 4 production trades"),
  dict(id="snap_back", verdict="RETIRED", note="purged 15 Oct (498366da); 1 production trade"),
  dict(id="momentum_ride", verdict="RETIRED", note="purged 15 Oct (498366da); 0 production trades"),
  dict(id="scalp_breakout_retest", verdict="RETIRED", note="purged 15 Oct (498366da); 10 production trades"),
  dict(id="range_sweep", verdict="RETIRED", note="removed 14 Oct (9d705a02); 39 production trades"),
]

PROD_NAME = {"key_level": "Key Level", "confluence_zone": "Confluence Zone", "supply": "Supply Demand", "demand": "Supply Demand",
             "order_block": "Order Block", "fvg": "FVG", "ifvg": "iFVG", "crt": "CRT", "flip_zone": "Flip Zone", "session_level": "Session Level",
             "trendline": "Trendline", "range_edge": "Range Edge Scalp", "break_retest": "Break & Retest", "fade_scalp": "Fade Scalp",
             "liquidity_sweep": "Liquidity Sweep", "impulse_pullback": "Impulse Pullback Scalp"}

QUALITY = {
  "key_level": ("Clamp01(stars/3)", "keylevel.go:346", "stars of the detector's own confluence (formula proven exact)", "collapses to 2 values: stars are floored at 2"),
  "confluence_zone": ("confirmed: Clamp01(stars/3); resting: Clamp01(MinGroups/4)", "confluencezone.go:238, :294", "confirmed: detector confluence; resting: count of independent fact groups", "resting is the constant 0.5 on 432 of 432 replay records"),
  "supply": ("confirmed: Clamp01(stars/3); resting: (strength + relevance + touch)/3", "technique_candidate.go:90; supply.go:331", "confirmed: stars; resting: zone.Strength, zone.Relevance, 1-0.15*touches", "resting Strength is the hard-coded 1 and Relevance the hard-coded Immediate for the M5 technique population, so resting quality depends on touches only (7 distinct values)"),
  "demand": ("as supply", "demand.go:283", "as supply", "as supply"),
  "order_block": ("as supply", "orderblock.go:313", "as supply", "as supply"),
  "fvg": ("confirmed: Clamp01(stars/3); resting: (strength + relevance + fill)/3", "fvg.go:314", "as supply with fill = 1-0.25*touches", "as supply"),
  "ifvg": ("Clamp01(SelectedStars/3)", "ifvg.go:182", "ifvg's own scorer; SessionContext is the constant true", "2 values"),
  "crt": ("Clamp01(stars/3)", "crt.go:151", "crt's own scorer", "2 values"),
  "flip_zone": ("Clamp01(stars/3) via TechniqueCandidate", "technique_candidate.go:90", "legacy detector stars", "2 values"),
  "session_level": ("Clamp01(SelectedStars/3)", "sessionlevel.go:217", "own scorer; SessionContext constant true", "2 values"),
  "trendline": ("Clamp01(stars/3) via TechniqueCandidate", "technique_candidate.go:90", "stars", "1 value (16 of 16 records have 3 stars)"),
  "range_edge": ("Clamp01(.25 + .06*min(barrier.Score,10) + .15*gradeA)", "rangeedge.go:233", "the barrier's own score and grade", "35 distinct values; correlation with stars 0.08"),
  "break_retest": ("constant PublishedOverallQuality = 0.75", "breakretest/config.go:82", "none: the measured setup is in Components only", "constant"),
  "fade_scalp": ("Clamp01(.55 + .15*gradeA + .05*min(touches,3))", "fadescalp.go", "grab grade and pool touches; 'reaction' and 'location' components are constants 1", "4 distinct values"),
  "liquidity_sweep": ("Clamp01(reclaim-bar body / ATR)", "liquiditysweep.go:130", "one candle's body relative to ATR; 'reclaim' component constant 1", "20 distinct values"),
  "impulse_pullback": ("Clamp01(displacement / (2*impulse_displacement_atr))", "impulsepullback.go:283", "the impulse leg's displacement only", "1 record in the replay"),
}


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--aeq-dist", required=True); ap.add_argument("--prod", required=True); ap.add_argument("--out-dir", required=True)
  a = ap.parse_args()
  dist = json.load(open(a.aeq_dist)); prod = json.load(open(a.prod))
  for s in STRATEGIES:
    s["production_trades_algo_auto"] = prod.get(PROD_NAME[s["id"]])
    s["production_note"] = "Supply and Demand share one production label" if s["id"] in ("supply", "demand") else ""
  json.dump({"description": "Per-strategy Python->Go verdicts. Positive and negative bars are stated per entry; vacuous coverage is listed, not hidden.",
             "reference_commits": {"profitable_week": "a1c77584", "frozen_oracle": "1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc"},
             "current_strategies": STRATEGIES, "retired": RETIRED},
            open(os.path.join(a.out_dir, "python-go-strategy-diff.json"), "w"), indent=1)
  rows = []
  for sid, (formula, loc, inputs, note) in QUALITY.items():
    stats = {k: v for k, v in dist.items() if k.split("|")[0] == sid}
    rows.append({"strategy": sid, "quality_overall_formula": formula, "source": loc, "inputs": inputs, "assessment": note, "replay_distribution": stats})
  json.dump({"description": "Quality.Overall inventory. No ranking is re-enabled and no score is invented: arbitration reads no quality (config quality_ranking: false).",
             "python_counterpart": "none: Python never had a per-strategy quality; its comparable quantity is confluence stars (proven exact vs both references on 62,208 factor combinations)",
             "arbitration": {"quality_ranking": False, "config": "config/analysis.yml:808, config/auto-algo.yml:43",
                             "residual_reads": ["multi_match.py:65 status-report representative only", "entry_overlap reservation stores quality, never decides on it"]},
             "strategies": rows},
            open(os.path.join(a.out_dir, "python-go-quality-diff.json"), "w"), indent=1)


main()
