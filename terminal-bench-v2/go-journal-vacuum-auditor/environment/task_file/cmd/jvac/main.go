package main

import (
	"flag"
	"fmt"
	"os"

	"jvac/internal/audit"
)

func main() {
	caseDir := flag.String("case", "", "case directory")
	outDir := flag.String("out", "", "output directory")
	flag.Parse()
	if *caseDir == "" || *outDir == "" {
		fmt.Fprintln(os.Stderr, "usage: jvac --case CASEDIR --out OUTDIR")
		os.Exit(2)
	}
	if err := audit.Run(*caseDir, *outDir); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
