# S14G: scoped live cutover (prepared, not executed)

`mode: go` and `consumer_enabled: true` are prepared as a **draft** configuration PR that must not be merged
until the S14F report has been reviewed. **Setting them grants no authority**: every scope remains Python-owned
until an accepted, fenced transfer (`app.scripts.analysis_authority`), which only the operator can request.

## The stop

```bash
python -m app.scripts.cutover_packet --symbol XAU --scope supply --equity <current equity> \
  --acceptance-report /reports/acceptance.json --images-json images.json --json packet.json --md packet.md
```

The packet prints the current status and epoch (read from the authority table, never assumed zero), the
evidence report and every blocking gate, deployed SHAs, the proposed reviewed scope, the maximum exposure the
existing controls permit at that equity (one group, every leg filled; the risk leg counted only if its gate is
on), open positions and tracked/pending plans, the exact rollback commands, and the projected activation
boundary. Anything it cannot read is `unavailable`. It contains **templates** for `accept` and `grant` with
placeholders for the operator's name and evidence reference and never executes them (AST-tested).

**Engineering stops here.** Do not run `accept` or `grant` with a generic evidence reference, on anyone's
behalf, or on implied permission.

## After the operator's explicit approval (operator steps)

1. `accept --symbol XAU --scope supply --evidence <ref you reviewed> --approved-by <you> --ttl-hours <n>`
2. `status`; read the current epoch.
3. `grant --symbol XAU --scope supply --expected-epoch <that epoch> --evidence <same ref> --actor <you> --reason <why> --drain-seconds 30`
   and verify neither Python nor Go publishes during the drain (S14H drill proves the fence does this).
4. Verify Go ownership and epoch; only new, confirmed, post-boundary opportunities proceed (freshness gate, S14B).
5. Observe the **first naturally valid** Go-origin plan end to end: Kafka → ledger → plan → cTrader → reconciliation; check
   stop protection, risk, card threading and journal provenance (`auto_trade_results.group_id` embeds the opportunity id).
   Do not manufacture an entry; a valid opportunity refused by a legitimate policy gate is not a failed cutover. A
   missing, unauthorised or duplicate order, or an unprotected fill, is.
6. Only after the first scope's production evidence is accepted, repeat for `demand`.

Unreviewed catalog strategies and every FX scope stay Python-owned.
