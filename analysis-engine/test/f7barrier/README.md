# F-7 opposing-barrier investigation tooling

Reproduces [docs/architecture/f7-opposing-barrier-ground-truth.md](../../../docs/architecture/f7-opposing-barrier-ground-truth.md).
Nothing here changes trading behavior, writes to production, or modifies a reference algorithm. Production is only ever read
(Redis `SCAN`/`LRANGE`/`GET`, Postgres inside a `READ ONLY` transaction).

| Step | Tool | Reads | Writes |
|---|---|---|---|
| 1 | production snapshot (read-only, see below) | Redis `auto_trade:lifecycle:go_opp_*`, Postgres `analysis_opportunity_events`, `auto_trade_fills`, `auto_trade_results` | `lifecycle.raw`, `opportunities.jsonl`, `fills.jsonl`, `results.jsonl` |
| 2 | `extract_events.py` | step 1 | `events.jsonl` (one admission decision per line), `bars.json` |
| 3 | `assign_bars.py` | `bars.json`, committed captures | one `*.bars.json` per capture (longest M5 history wins) |
| 4 | `dump_test.go` (`F7_BARS`, `F7_CAPTURES`, `F7_DUMP_DIR`) | captures | `*.go.jsonl`: Model C book + raw zones per requested bar |
| 5 | `oracle_books.py` on a git worktree of a frozen commit | captures | `*.<tag>.jsonl`: Model A (scanner book) and Model B (TradePlan-time book) |
| 6 | `credibility.py` (run from `algo-bot`) | step 4, captures | `extra_books.jsonl` (labelled-reference and Model D candidate books), `credibility.jsonl` (per-zone labels) |
| 7 | `replay_admission.py` (run from `algo-bot`) | steps 2, 4, 5, 6 | `replay.jsonl`: the real `structural_target_room` decision per event per book |
| 8 | `simulate.py` | step 2, captures | `sim.jsonl`: causal stop / +1R / +2R race (hypothetical) |
| 9 | `anatomy.py`, `analyze.py`, `cases.py` | steps 4-8 | `anatomy.json`, `results.json`, `disagreement.csv`, `cases/*.png` |

```
git worktree add /tmp/py-a1c77584 a1c77584              # frozen scanner + worker (Models A and B)
git worktree add /tmp/py-1c9f323  1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc   # last revision with the Python analysis code (Model A only)

# 1  read-only production snapshot (the queries are in the report, section "Data")
# 2-3
python3 analysis-engine/test/f7barrier/extract_events.py --lifecycle lifecycle.raw --opps opportunities.jsonl \
    --fills fills.jsonl --results results.jsonl --out events.jsonl --bars bars.json
python3 analysis-engine/test/f7barrier/assign_bars.py --bars bars.json --testdata analysis-engine/testdata --out-dir assign
# 4
cd analysis-engine && F7_BARS=$PWD/../bars.json F7_CAPTURES=<comma list> F7_DUMP_DIR=/tmp/f7 go test ./test/f7barrier -run TestDumpBooks
# 5 (per capture; run outside app/analysis, its types.py shadows the standard library)
python analysis-engine/test/f7barrier/oracle_books.py --oracle /tmp/py-a1c77584 --capture <capture.json> --bars assign/<capture>.bars.json --out /tmp/f7/<capture>.a1c77584.jsonl
# 6-7 (from algo-bot, Python 3.12 with the repo requirements)
cd algo-bot
python ../analysis-engine/test/f7barrier/credibility.py --events events.jsonl --books-dir /tmp/f7 --out-books /tmp/f7/extra_books.jsonl --out-labels /tmp/f7/credibility.jsonl
python ../analysis-engine/test/f7barrier/replay_admission.py --events events.jsonl --books-dir /tmp/f7 --oracle-tag a1c77584 --extra-books /tmp/f7/extra_books.jsonl --out /tmp/f7/replay.jsonl
# 8-9
python3 analysis-engine/test/f7barrier/simulate.py --events events.jsonl --opps opportunities.jsonl --testdata analysis-engine/testdata --out sim.jsonl
python3 analysis-engine/test/f7barrier/anatomy.py --replay replay.jsonl --events events.jsonl --books-dir /tmp/f7 --out anatomy.json
python3 analysis-engine/test/f7barrier/analyze.py --replay replay.jsonl --events events.jsonl --books-dir /tmp/f7 --oracle-tag a1c77584 \
    --sim sim.jsonl --anatomy anatomy.json --labels /tmp/f7/credibility.jsonl --out results.json --csv disagreement.csv
```

## Proving a change moved no decision

`decisions_test.go` (`TestDumpDecisionInputs`) records, for every M5 bar of the committed captures that produced an opportunity, the
opportunities (full-struct sha256 and geometry) and the barriers the publisher would write. Run it on two revisions and compare
(`compare_decisions.py`, from `algo-bot`): identical opportunities, identical barriers apart from the additive `members`, and
identical decisions from the real `filter_shared_boundary_opposing_entries` + `evaluate_structural_target_room` on both books
read through the real `parse_zone_book`. It also checks that members tile their barrier and that the decisive member named for a
block belongs to the barrier that blocked.

```
git worktree add /tmp/before origin/master && cp analysis-engine/test/f7barrier/decisions_test.go /tmp/before/analysis-engine/test/f7barrier/
(cd /tmp/before/analysis-engine && F7_DECISIONS_DIR=/tmp/dec-before F7_CAPTURES=<list> go test ./test/f7barrier -run TestDumpDecisionInputs)
(cd analysis-engine && F7_DECISIONS_DIR=/tmp/dec-after F7_CAPTURES=<list> go test ./test/f7barrier -run TestDumpDecisionInputs)
(cd algo-bot && python ../analysis-engine/test/f7barrier/compare_decisions.py --before /tmp/dec-before --after /tmp/dec-after --out compare.json)
```

### Comparing two rule revisions

`decide_dump.py` (from `algo-bot`, once per code revision and book dump) writes the worker's own admission decision, the deciding
barrier and its decisive member for every replayed opportunity; `compare_corrections.py` joins the four combinations (old/new rule x
old/new books) and reports every changed decision with its cause, the changes by strategy and symbol, and the hypothetical stop / +1R /
+2R race of newly admitted, newly blocked, unchanged-admitted and still-blocked groups. `validity_test.go` (`F7_VALIDITY_OUT`) counts live
zones whose own `zone.NotInvalidated` rule says they are broken.

The tools' own logic (race, bar mapping, union length, divergence classification, clustered statistics) is covered by
`algo-bot/tests/test_f7_barrier_tools.py`.
