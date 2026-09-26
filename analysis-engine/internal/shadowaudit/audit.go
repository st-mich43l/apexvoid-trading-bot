// Package shadowaudit reconciles bounded Analysis Engine lifecycle events.
// It is transport-neutral so fixture tests and Kafka reads share one model.
package shadowaudit

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"

	transport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

const (
	CreatedEvent  = "analysis.opportunity.v1"
	TerminalEvent = "analysis.opportunity.invalidated.v1"
)

type Boundary struct {
	Topic     string `json:"topic"`
	Partition int32  `json:"partition"`
	Start     int64  `json:"start_offset"`
	End       int64  `json:"end_offset"`
}

type Record struct {
	Topic     string
	Partition int32
	Offset    int64
	Value     []byte
}
type Sample struct {
	Topic         string `json:"topic"`
	Partition     int32  `json:"partition"`
	Offset        int64  `json:"offset"`
	EventID       string `json:"event_id,omitempty"`
	OpportunityID string `json:"opportunity_id,omitempty"`
	Error         string `json:"error"`
}
type Distribution struct {
	Count int64 `json:"count"`
	Min   int64 `json:"min"`
	P50   int64 `json:"p50"`
	P95   int64 `json:"p95"`
	Max   int64 `json:"max"`
}

type Report struct {
	Boundaries                  []Boundary     `json:"boundaries"`
	BuildVersion                string         `json:"build_version"`
	CleanEpoch                  bool           `json:"clean_epoch"`
	FirstOccurredAt             int64          `json:"first_occurred_at"`
	LastOccurredAt              int64          `json:"last_occurred_at"`
	Records                     int            `json:"records"`
	InvalidRecords              int            `json:"invalid_records"`
	Creations                   int            `json:"creations"`
	Terminals                   int            `json:"terminals"`
	MatchedTerminals            int            `json:"matched_terminals"`
	BaselineMatched             int            `json:"baseline_matched_terminals"`
	UnknownPreWindow            int            `json:"unknown_pre_window"`
	OrphanTerminals             int            `json:"orphan_terminals"`
	DuplicateCreations          int            `json:"duplicate_semantic_creations"`
	DuplicateTerminals          int            `json:"duplicate_semantic_terminals"`
	IdentityDrift               int            `json:"identity_drift"`
	BySymbol                    map[string]int `json:"by_symbol"`
	ByStrategy                  map[string]int `json:"by_strategy"`
	ByDirection                 map[string]int `json:"by_direction"`
	ByTimeframe                 map[string]int `json:"by_timeframe"`
	TerminalReasons             map[string]int `json:"terminal_reasons"`
	ConfigFingerprints          map[string]int `json:"config_fingerprints"`
	OpportunityAgeSeconds       Distribution   `json:"opportunity_age_seconds"`
	PublicationLagSeconds       Distribution   `json:"publication_lag_seconds"`
	RetryMetricsAvailable       bool           `json:"retry_metrics_available"`
	SuppressionMetricsAvailable bool           `json:"suppression_metrics_available"`
	Samples                     []Sample       `json:"samples,omitempty"`
}

type Audit struct {
	report          Report
	baseline        map[string]struct{}
	created         map[string]string
	terminals       map[string]struct{}
	terminalSamples map[string]Sample
	ages, lags      []int64
}

func New(boundaries []Boundary, baseline []string, cleanEpoch bool) *Audit {
	b := make(map[string]struct{}, len(baseline))
	for _, id := range baseline {
		b[id] = struct{}{}
	}
	return &Audit{report: Report{Boundaries: append([]Boundary(nil), boundaries...), CleanEpoch: cleanEpoch, BySymbol: map[string]int{}, ByStrategy: map[string]int{}, ByDirection: map[string]int{}, ByTimeframe: map[string]int{}, TerminalReasons: map[string]int{}, ConfigFingerprints: map[string]int{}}, baseline: b, created: map[string]string{}, terminals: map[string]struct{}{}, terminalSamples: map[string]Sample{}}
}

func (a *Audit) Add(record Record) {
	a.report.Records++
	var env transport.Envelope
	if json.Unmarshal(record.Value, &env) != nil || env.Validate() != nil {
		a.invalid(record, "invalid envelope")
		return
	}
	if env.EventType != record.Topic {
		a.invalid(record, "event_type does not match topic")
		return
	}
	if env.ConfigFingerprint == "" || env.ConfigVersion < 1 {
		a.invalid(record, "missing configuration provenance")
		return
	}
	a.report.ConfigFingerprints[env.ConfigFingerprint]++
	if a.report.FirstOccurredAt == 0 || env.OccurredAt < a.report.FirstOccurredAt {
		a.report.FirstOccurredAt = env.OccurredAt
	}
	if env.OccurredAt > a.report.LastOccurredAt {
		a.report.LastOccurredAt = env.OccurredAt
	}
	a.lags = append(a.lags, env.ProducedAt-env.OccurredAt)
	switch env.EventType {
	case CreatedEvent:
		var p transport.OpportunityPayload
		if json.Unmarshal(env.Payload, &p) != nil {
			a.invalid(record, "invalid opportunity payload")
			return
		}
		var raw map[string]json.RawMessage
		_ = json.Unmarshal(env.Payload, &raw)
		if _, hasFormedAt := raw["formed_at"]; !hasFormedAt {
			p.FormedAt = p.CreatedAt
		}
		if !validCreation(p) {
			a.invalid(record, "invalid opportunity payload")
			return
		}
		a.report.Creations++
		a.report.BySymbol[p.Symbol]++
		a.report.ByStrategy[p.Strategy]++
		a.report.ByDirection[p.Direction]++
		if p.Timeframe == "" {
			a.report.ByTimeframe["unknown_pre_s11"]++
		} else {
			a.report.ByTimeframe[p.Timeframe]++
		}
		fingerprint := geometryFingerprint(p)
		if prior, ok := a.created[p.ID]; ok {
			a.report.DuplicateCreations++
			if prior != fingerprint {
				a.report.IdentityDrift++
			}
		} else {
			a.created[p.ID] = fingerprint
		}
		a.ages = append(a.ages, env.OccurredAt-p.FormedAt)
	case TerminalEvent:
		var p transport.OpportunityInvalidatedPayload
		if json.Unmarshal(env.Payload, &p) != nil || p.OpportunityID == "" || p.ReasonCode == "" || p.InvalidatedAt < 0 {
			a.invalid(record, "invalid terminal payload")
			return
		}
		a.report.Terminals++
		a.report.TerminalReasons[p.ReasonCode]++
		if _, ok := a.terminals[p.OpportunityID]; ok {
			a.report.DuplicateTerminals++
			return
		}
		a.terminals[p.OpportunityID] = struct{}{}
		a.terminalSamples[p.OpportunityID] = Sample{Topic: record.Topic, Partition: record.Partition, Offset: record.Offset, EventID: env.EventID, OpportunityID: p.OpportunityID, Error: "orphan terminal"}
	default:
		a.invalid(record, "unsupported event type")
	}
}

func (a *Audit) Report() Report {
	report := a.report
	report.OpportunityAgeSeconds = distribution(a.ages)
	report.PublicationLagSeconds = distribution(a.lags)
	ids := make([]string, 0, len(a.terminals))
	for id := range a.terminals {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		if _, ok := a.created[id]; ok {
			report.MatchedTerminals++
		} else if _, ok := a.baseline[id]; ok {
			report.BaselineMatched++
		} else if report.CleanEpoch {
			report.OrphanTerminals++
			if len(report.Samples) < 20 {
				report.Samples = append(report.Samples, a.terminalSamples[id])
			}
		} else {
			report.UnknownPreWindow++
		}
	}
	return report
}

func (a *Audit) SetBuildVersion(version string) { a.report.BuildVersion = version }
func (a *Audit) invalid(r Record, reason string) {
	a.report.InvalidRecords++
	a.sample(r, "", "", reason)
}
func (a *Audit) sample(r Record, eventID, opportunityID, reason string) {
	if len(a.report.Samples) < 20 {
		a.report.Samples = append(a.report.Samples, Sample{r.Topic, r.Partition, r.Offset, eventID, opportunityID, reason})
	}
}

func validCreation(p transport.OpportunityPayload) bool {
	return p.ID != "" && p.Strategy != "" && p.Symbol != "" && (p.Direction == "BUY" || p.Direction == "SELL") && p.Entry.Low <= p.Entry.High && p.FormedAt >= 0 && p.CreatedAt >= p.FormedAt && p.ExpiresAt > p.CreatedAt && p.AlgorithmVersion.Structure != "" && p.AlgorithmVersion.Liquidity != "" && len(p.Targets) > 0 && len(p.Evidence) > 0
}
func geometryFingerprint(p transport.OpportunityPayload) string {
	data, _ := json.Marshal([]any{p.Strategy, p.Symbol, p.Direction, p.Entry, p.Invalidation, p.Targets})
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:8])
}
func distribution(values []int64) Distribution {
	if len(values) == 0 {
		return Distribution{}
	}
	values = append([]int64(nil), values...)
	sort.Slice(values, func(i, j int) bool { return values[i] < values[j] })
	return Distribution{int64(len(values)), values[0], percentile(values, .5), percentile(values, .95), values[len(values)-1]}
}
func percentile(values []int64, p float64) int64 { return values[int(float64(len(values)-1)*p+.5)] }

func Human(r Report) string {
	return fmt.Sprintf("Shadow audit: records=%d valid=%d creations=%d terminals=%d\nLifecycle: matched=%d baseline=%d unknown_pre_window=%d orphan=%d duplicate_creations=%d duplicate_terminals=%d identity_drift=%d\nLatency seconds: age[p50=%d p95=%d max=%d] publish[p50=%d p95=%d max=%d]\nConfig fingerprints: %v\nRetry/suppression metrics: unavailable from Kafka records; inspect engine telemetry for this epoch.\n", r.Records, r.Records-r.InvalidRecords, r.Creations, r.Terminals, r.MatchedTerminals, r.BaselineMatched, r.UnknownPreWindow, r.OrphanTerminals, r.DuplicateCreations, r.DuplicateTerminals, r.IdentityDrift, r.OpportunityAgeSeconds.P50, r.OpportunityAgeSeconds.P95, r.OpportunityAgeSeconds.Max, r.PublicationLagSeconds.P50, r.PublicationLagSeconds.P95, r.PublicationLagSeconds.Max, r.ConfigFingerprints)
}
