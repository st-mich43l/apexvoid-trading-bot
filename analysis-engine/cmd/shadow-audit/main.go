// Command shadow-audit reads explicit Kafka offsets without a consumer group.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/shadowaudit"
	"github.com/twmb/franz-go/pkg/kgo"
)

type boundariesFlag []shadowaudit.Boundary

func (f *boundariesFlag) String() string { return fmt.Sprint([]shadowaudit.Boundary(*f)) }
func (f *boundariesFlag) Set(value string) error {
	parts := strings.Split(value, ":")
	if len(parts) != 4 {
		return fmt.Errorf("boundary must be topic:partition:start:end")
	}
	p, e := strconv.ParseInt(parts[1], 10, 32)
	if e != nil {
		return e
	}
	start, e := strconv.ParseInt(parts[2], 10, 64)
	if e != nil {
		return e
	}
	end, e := strconv.ParseInt(parts[3], 10, 64)
	if e != nil {
		return e
	}
	if parts[0] == "" || p < 0 || start < 0 || end <= start {
		return fmt.Errorf("invalid boundary %q", value)
	}
	*f = append(*f, shadowaudit.Boundary{Topic: parts[0], Partition: int32(p), Start: start, End: end})
	return nil
}

func main() {
	var brokers, output, baselinePath, buildVersion string
	var cleanEpoch bool
	var bounds boundariesFlag
	flag.StringVar(&brokers, "brokers", "localhost:9092", "comma-separated Kafka brokers")
	flag.Var(&bounds, "partition", "topic:partition:start:end, repeatable; end exclusive")
	flag.StringVar(&baselinePath, "baseline", "", "optional JSON array of pre-window creation IDs")
	flag.StringVar(&buildVersion, "build-version", "unknown", "deployed image digest or Git SHA")
	flag.BoolVar(&cleanEpoch, "clean-epoch", false, "classify unmatched terminals as orphans")
	flag.StringVar(&output, "json", "", "write machine-readable report")
	flag.Parse()
	if len(bounds) == 0 {
		fatal(fmt.Errorf("at least one -partition boundary is required"))
	}
	baseline, e := loadBaseline(baselinePath)
	if e != nil {
		fatal(e)
	}
	report, e := read(context.Background(), strings.Split(brokers, ","), bounds, baseline, cleanEpoch, buildVersion)
	if e != nil {
		fatal(e)
	}
	encoded, _ := json.MarshalIndent(report, "", "  ")
	if output != "" {
		if e = os.WriteFile(output, append(encoded, '\n'), 0640); e != nil {
			fatal(e)
		}
	}
	fmt.Print(shadowaudit.Human(report))
	if output == "" {
		fmt.Println(string(encoded))
	}
}

func read(ctx context.Context, brokers []string, bounds []shadowaudit.Boundary, baseline []string, cleanEpoch bool, buildVersion string) (shadowaudit.Report, error) {
	assign := map[string]map[int32]kgo.Offset{}
	ends := map[string]map[int32]int64{}
	done := map[string]map[int32]bool{}
	for _, b := range bounds {
		if assign[b.Topic] == nil {
			assign[b.Topic] = map[int32]kgo.Offset{}
			ends[b.Topic] = map[int32]int64{}
			done[b.Topic] = map[int32]bool{}
		}
		assign[b.Topic][b.Partition] = kgo.NewOffset().At(b.Start)
		ends[b.Topic][b.Partition] = b.End
	}
	client, e := kgo.NewClient(kgo.SeedBrokers(brokers...), kgo.ClientID("apexvoid-shadow-audit"), kgo.ConsumePartitions(assign))
	if e != nil {
		return shadowaudit.Report{}, e
	}
	defer client.Close()
	audit := shadowaudit.New(bounds, baseline, cleanEpoch)
	audit.SetBuildVersion(buildVersion)
	for !allDone(bounds, done) {
		pollCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		fetches := client.PollFetches(pollCtx)
		cancel()
		if errs := fetches.Errors(); len(errs) > 0 {
			return shadowaudit.Report{}, fmt.Errorf("Kafka fetch: %v", errs)
		}
		if fetches.Empty() {
			return shadowaudit.Report{}, fmt.Errorf("timed out before all end offsets were reached")
		}
		fetches.EachRecord(func(r *kgo.Record) {
			end := ends[r.Topic][r.Partition]
			if r.Offset >= end {
				done[r.Topic][r.Partition] = true
				return
			}
			audit.Add(shadowaudit.Record{Topic: r.Topic, Partition: r.Partition, Offset: r.Offset, Value: r.Value})
			if r.Offset+1 >= end {
				done[r.Topic][r.Partition] = true
			}
		})
	}
	return audit.Report(), nil
}
func allDone(bounds []shadowaudit.Boundary, done map[string]map[int32]bool) bool {
	for _, b := range bounds {
		if !done[b.Topic][b.Partition] {
			return false
		}
	}
	return true
}
func loadBaseline(path string) ([]string, error) {
	if path == "" {
		return nil, nil
	}
	data, e := os.ReadFile(path)
	if e != nil {
		return nil, e
	}
	var ids []string
	e = json.Unmarshal(data, &ids)
	return ids, e
}
func fatal(e error) { fmt.Fprintln(os.Stderr, "shadow-audit:", e); os.Exit(1) }
