// Command strategy-compare compares normalized Python and Go JSONL exports.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/shadowcompare"
	"os"
)

func main() {
	p := flag.String("python", "", "Python observation JSONL")
	g := flag.String("go", "", "Go observation JSONL")
	out := flag.String("json", "", "output report")
	tol := flag.Int64("tolerance-seconds", 300, "maximum confirmation-time delta")
	disp := flag.String("dispositions", "", "optional JSON map pythonID|goID to approved category")
	flag.Parse()
	if *p == "" || *g == "" {
		fatal(fmt.Errorf("-python and -go are required"))
	}
	py, e := read(*p, "python")
	if e != nil {
		fatal(e)
	}
	goObs, e := read(*g, "go")
	if e != nil {
		fatal(e)
	}
	dispositions := map[string]string{}
	if *disp != "" {
		b, e := os.ReadFile(*disp)
		if e != nil {
			fatal(e)
		}
		if e = json.Unmarshal(b, &dispositions); e != nil {
			fatal(e)
		}
		for pair, category := range dispositions {
			if !shadowcompare.ValidCategory(category) {
				fatal(fmt.Errorf("disposition %q has unsupported category %q", pair, category))
			}
		}
	}
	report := shadowcompare.Compare(py, goObs, *tol, dispositions)
	b, _ := json.MarshalIndent(report, "", "  ")
	if *out != "" {
		if e = os.WriteFile(*out, append(b, '\n'), 0640); e != nil {
			fatal(e)
		}
	} else {
		fmt.Println(string(b))
	}
}
func read(path, expectedEngine string) ([]shadowcompare.Observation, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	var out []shadowcompare.Observation
	s := bufio.NewScanner(f)
	s.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for line := 1; s.Scan(); line++ {
		var o shadowcompare.Observation
		if e := json.Unmarshal(s.Bytes(), &o); e != nil {
			return nil, fmt.Errorf("%s:%d: %w", path, line, e)
		}
		if e := o.Validate(); e != nil {
			return nil, fmt.Errorf("%s:%d: %w", path, line, e)
		}
		if o.Engine != expectedEngine {
			return nil, fmt.Errorf("%s:%d: engine=%q, want %q", path, line, o.Engine, expectedEngine)
		}
		out = append(out, o)
	}
	return out, s.Err()
}
func fatal(e error) { fmt.Fprintln(os.Stderr, "strategy-compare:", e); os.Exit(1) }
