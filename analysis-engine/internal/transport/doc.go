// Package transport contains external adapters only. `redis` owns Analysis
// Engine's live market-data input; `kafka` is its producer-only durable
// business-event adapter. Domain packages never import either one; see
// docs/architecture.md and docs/analysis-engine.md.
package transport
