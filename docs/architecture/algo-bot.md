# Algo Bot (Python)

Algo Bot is the execution and control plane. It consumes Go Analysis Engine
opportunity events and decides whether current market and account conditions
permit a TradePlan.

## Responsibilities

- decode and persist the Kafka opportunity lifecycle;
- apply freshness, quote, spread, session, news, duplicate, exposure, and risk
  policy;
- build and publish Redis TradePlans;
- manage Telegram notifications, journal, statistics, and operator commands;
- support the separate manual algo workflow.

The service does not scan markets, rebuild structure, construct zones, or
generate technical opportunities. Missing technical facts are a contract or
Analysis Engine issue, not a reason to rerun a detector in Python.

## Main modules

- `app/analysis_client/`: validated Go opportunity consumer and lifecycle store.
- `app/autotrade/`: eligibility, policy, duplicate guards, sizing, risk,
  TradePlan construction, and execution-event handling.
- `app/configuration/`: typed configuration loader and validation.
- `app/bot/`: Telegram commands and notifications.
- `app/persistence/`: PostgreSQL and Redis state.
- `app/scalping/`: scalping outcome/accounting helpers.
- `tests/`: execution, contract, boundary, risk, and lifecycle coverage.

## Local validation

```bash
PYTHONPATH=. pytest -q $(grep -E '^tests/' tests/ci_autotrade_paths.txt)
```

The final TradePlan publisher accepts only the supported Go opportunity
contract and still performs all execution-time safety checks before Redis
publication.
