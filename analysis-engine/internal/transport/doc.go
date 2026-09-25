// Package transport contains external adapters only. `redis` owns Analysis
// Engine's live market-data input; `kafka` is its producer-only durable
// business-event adapter. Domain packages never import either one; see
// ADR-010 and docs/architecture/dependency-rules.md.
package transport
