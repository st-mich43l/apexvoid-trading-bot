# S11 Python-Go Comparison Runbook

`cmd/strategy-compare` compares normalized observations generated from the
same closed-bar sequence. It matches semantic setups by symbol, timeframe,
strategy, direction and confirmation-time tolerance, rather than requiring
internal IDs or byte-for-byte architecture to agree.

Each JSONL row must contain: engine, ID, symbol, timeframe, strategy,
direction, input/config fingerprints, formation/confirmation timestamps,
entry band, invalidation, targets, structure and zone fingerprints,
strategy-specific quality, MFE, MAE and technical outcome. Exporters on both
sides must fingerprint the exact same ordered OHLC input. Invalid/incomplete
rows fail closed.

```bash
go run ./cmd/strategy-compare \
  -python /reports/python-observations.jsonl \
  -go /reports/go-observations.jsonl \
  -tolerance-seconds 300 \
  -dispositions /reports/approved-dispositions.json \
  -json /reports/s11-python-go-comparison.json
```

The optional disposition file maps `pythonID|goID` to one of
`intended_redesign`, `confirmed_regression`, `configuration_difference`,
`data_mismatch`, or `unresolved_discrepancy`. Without an explicit owner-reviewed
disposition, input/config fingerprints classify those two objective cases and
all other differences remain unresolved. The report includes unmatched setup
detection and differences in structure, zones, timing, entry, invalidation,
targets, quality, outcome, MFE and MAE.

The required acceptance capture is XAU, one non-JPY FX pair and one JPY pair
over multiple market sessions, with exact build/config/boundaries. This tool
does not activate a Kafka consumer or trading path.
