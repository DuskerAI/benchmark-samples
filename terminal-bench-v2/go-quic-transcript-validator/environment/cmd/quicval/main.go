package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"qtrace.local/quicval/internal/qtrace"
)

func main() {
	tracePath := flag.String("trace", "/app/quicval/fixtures/customer-alpha.qtr", "trace bundle path")
	outPath := flag.String("out", "/app/quicval/out/transcript-report.json", "report output path")
	flag.Parse()

	report, err := qtrace.ValidateFile(*tracePath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "quicval: %v\n", err)
		os.Exit(2)
	}

	if err := os.MkdirAll(filepath.Dir(*outPath), 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "quicval: %v\n", err)
		os.Exit(2)
	}
	f, err := os.Create(*outPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "quicval: %v\n", err)
		os.Exit(2)
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(report); err != nil {
		fmt.Fprintf(os.Stderr, "quicval: %v\n", err)
		os.Exit(2)
	}
}
