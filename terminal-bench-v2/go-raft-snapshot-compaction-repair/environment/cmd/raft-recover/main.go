package main

import (
	"flag"
	"fmt"
	"os"

	"raftbench/internal/recovery"
)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "recover" {
		fmt.Fprintln(os.Stderr, "usage: raft-recover recover --case <dir> --out <dir>")
		os.Exit(2)
	}
	fs := flag.NewFlagSet("recover", flag.ExitOnError)
	caseDir := fs.String("case", "/app/cases/public", "case directory")
	outDir := fs.String("out", "/app/output", "output directory")
	if err := fs.Parse(os.Args[2:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := recovery.Run(*caseDir, *outDir); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
