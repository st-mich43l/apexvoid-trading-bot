# S11 Shadow Audit Runbook

`cmd/shadow-audit` reads explicitly bounded Kafka partitions with direct
partition assignment. It has no consumer group and never commits offsets or
touches trading state.

Record the deployment Git SHA/image digest, configuration fingerprint, and
start offsets immediately after deploying the S11 build. After the required
multi-session window, record exclusive end offsets and run:

```bash
go run ./cmd/shadow-audit \
  -brokers kafka:9092 \
  -build-version <git-sha-or-image-digest> \
  -clean-epoch \
  -partition analysis.opportunity.v1:0:<start>:<end> \
  -partition analysis.opportunity.v1:1:<start>:<end> \
  -partition analysis.opportunity.invalidated.v1:0:<start>:<end> \
  -json /reports/s11-shadow-audit.json
```

Repeat `-partition` for every partition, including zero-record partitions only
when their start/end differ. End offsets are exclusive. Use `-clean-epoch` only
when the boundary starts before any creation produced by the deployed build.
For an arbitrary window, omit it and pass `-baseline ids.json`, a JSON array of
creation IDs known before the window. Unmatched terminals are then reported as
`unknown_pre_window`, not falsely called orphans.

The JSON report contains exact boundaries, observed event range, build/config
provenance, counts by symbol/strategy/direction/timeframe, terminal reasons,
lifecycle reconciliation, duplicate identities, geometry drift, and age/lag
distributions. Kafka records do not contain retry/suppression counters; collect
the named Analysis Engine telemetry counters over the same wall-clock epoch and
attach them to the acceptance report.
