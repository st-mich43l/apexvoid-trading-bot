# ADR-001: Analysis Engine is a standalone Go service

## Status
Accepted (2026-09-22, architecture-definition task).

## Context
`algo-bot` today computes all technical market structure in-process
(`app/analysis/*`, ~3.7MB) alongside trading orchestration
(`app/autotrade/*`, ~6.0MB) and Telegram/control-plane code. This is CPU-
heavy, deterministic, single-threaded-per-event math sharing a process
with I/O-bound orchestration and third-party API calls (Telegram, cTrader
Open API). `docs/go-analysis-migration-audit.md` already documents the
computation graph and started a Go port (`analysis-engine/`, Stage 1 +
partial Stage 2: `market`, `indicator`, `config` packages, parity-tested
against real Python output).

## Decision
`analysis-engine` is a separate, standalone Go service/module with its own
`go.mod`, deployable independently of `algo-bot` and `ctrader-engine`. It
owns all deterministic technical market interpretation (§2 of the
architecture task) and nothing else. It is not a library imported into
`algo-bot`'s Python process — the two communicate over an event contract
(`analysis.opportunity.v1`, see ADR-004/007), not an in-process call.

## Consequences
- CPU-bound analysis work can scale independently of Telegram/orchestration
  I/O, and a crash or slowdown in one does not take down the other.
- `algo-bot` cannot silently recompute technical structure just because
  the function is "right there in the same process" — the event boundary
  makes that a deliberate act, not an accident.
- Python remains the reference implementation and stays authoritative
  until a shadow-mode parity run earns cutover (existing project rule,
  `analysis-engine/README.md`); this ADR does not change that gate.
- Both services must independently read Configuration V3
  (`config/apexvoid.yml`), never share config through a private channel.
