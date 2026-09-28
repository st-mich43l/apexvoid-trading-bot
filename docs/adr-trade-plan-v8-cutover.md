# ADR: TradePlan V8 — sole autonomous contract

## Status

Accepted. Prior TradePlan versions are fully removed from production code,
contracts, and tests. Historical rationale lives only in `CHANGELOG.md` past
entries.

## Decision

TradePlan V8 is the only autonomous trade-planning contract across
`algo-bot` and `ctrader-engine`. Python owns every planned value (entry,
absolute stop, absolute targets); C# parses, validates shape, and executes
without recomputing route or stop.

## Identity

| Field | Value |
| --- | --- |
| `TRADE_PLAN_VERSION` / `TradePlanContract.Version` | `8` |
| `TRADE_PLAN_SUPPORTED_VERSIONS` / `SupportedVersions` | `{8}` only |
| Plan id | `v8:{match_id}` |
| Broker ownership comment | `v8\|…` |
| Exposure source | `v8_plan` |
| `AUTO_TRADE_CONTRACT_MODE` (live) | `v8_only` |
| Notify dedup | `auto_trade:v8_notify:*` only |
| Thesis identity salt | `"thesis"` (`thesis_id()`) |

`legacy_v6` remains available on the C# record/default for mechanical V6
manage tests and open-position recovery. It is not a live autonomous
publish mode.

## Unchanged boundary

- Entry / stop / target ownership (Python declares, C# executes).
- Opposing-room geometry on the non-scalp publish path: shared-boundary
  filter, overlap filter for stacked map vs candidate bands, and
  zone-proximal room reference.
- Scalp opposing bypass via `match_bypasses_opposing_structure`.
- V6 retained only for legacy open-position manage and manual `/algo`.

## Shared fixture

`contracts/autotrade/trade-plan-v8.json` is the single shared contract
table for Python (`test_trade_plan_v8_contract.py`) and C#
(`TradePlanV8ContractTests.cs`).
