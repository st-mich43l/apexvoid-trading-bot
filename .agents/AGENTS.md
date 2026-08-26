# AGENTS.md

Rules for AI agents working on **apexvoid-trading-bot**. Read before changing code, tests, or CI.

## CI is simple — do not mess with it

The **Autotrade Integrity** workflow (`.github/workflows/autotrade-integrity.yml`) exists only to verify that the repo **builds and the allowlisted autotrade tests run**. It is not a full regression suite and not something to “optimize”, expand, or rewrite on every PR.

### What CI runs

| Job | Purpose |
|-----|---------|
| **Repository checks** | `docker compose config -q` — stack file is valid |
| **Python autotrade suite** | Allowlisted pytest paths from `algo-bot/tests/ci_autotrade_paths.txt` |
| **C# autotrade suite** | `dotnet build` + filtered `dotnet test` on ctrader-engine |

That is all. No extra gates, no “run everything”, no new jobs unless the owner explicitly asks.

### Hard rules — CI files and scope

**Do not change these unless the user explicitly asks:**

- `.github/workflows/autotrade-integrity.yml`
- `.github/workflows/deploy.yml`
- `algo-bot/tests/ci_autotrade_paths.txt`

**Do not:**

- Add or remove paths from `ci_autotrade_paths.txt` to green a PR
- Add `--deselect`, `-k`, `-m`, `skip`, or `xfail` to the workflow or to allowlisted tests to hide failures
- Replace the allowlist with “run all tests” or swap in a different test command
- Weaken, delete, or rewrite assertions in **unrelated** allowlisted tests because CI failed
- Pin new feature tests into CI by default — new tests live under `algo-bot/tests/`; CI inclusion is a separate, owner-driven decision
- Touch C# test filters in the workflow except when the owner requests it

**If CI fails on a PR you opened:**

1. Reproduce locally with the **same command CI uses** (see workflow file).
2. Fix **your code** or **tests you added/changed** for the feature.
3. Do **not** “fix CI” by editing the workflow or allowlist.

### Local check before push

From `algo-bot/` (match CI env as closely as practical):

```bash
pip install -r requirements.txt -r requirements-dev.txt
export REAL_REDIS_URL=redis://127.0.0.1:6379/15
export DATABASE_URL=postgresql://apexvoid:apexvoid@127.0.0.1:55432/signals
export PYTHONPATH=.

pytest -q \
  $(grep -E '^tests/' tests/ci_autotrade_paths.txt) \
  --deselect=tests/test_trade_plan_builder.py::test_stop_inside_opposing_zone_surfaces_precise_reason_and_evidence \
  --deselect=tests/test_publish_trade_plan_v8.py::test_final_v7_gate_rejects_entry_inside_opposing_structure \
  --deselect=tests/test_publish_trade_plan_v8.py::test_final_v7_gate_rejects_sell_from_demand_containment \
  --deselect=tests/test_publish_trade_plan_v8.py::test_range_edge_without_target_room_still_hits_opposing
```

Also run tests for **your** change if they are outside the allowlist (e.g. `tests/test_mad_phase.py`). Those are for local/PR confidence; they do not replace fixing allowlisted failures.

## Tests you write

- Prefer **smoke / behavior** checks: service not rejected, event published, volume &gt; 0, expected strategy family — not brittle pins on comment strings or incidental internals.
- When **you** change behavior, update **your** tests — do not leave stale assertions.
- See `.cursor/rules/tests-keep-simple.mdc` for test style on touched files.
- This file lives at **`.agents/AGENTS.md`** (not repo root).

## General PR hygiene

- One focused change per PR; no drive-by refactors.
- Update `CHANGELOG.md` under `## Unreleased` when behavior or operators would notice.
- Do not push to `master` directly; use PRs.
- Do not commit unless the user asks.
