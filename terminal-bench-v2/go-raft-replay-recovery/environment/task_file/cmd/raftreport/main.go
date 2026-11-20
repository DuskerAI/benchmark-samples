package main

import (
	"flag"
	"fmt"
	"os"

	"raftreplay/internal/replay"
)

func main() {
	input := flag.String("input", "", "trace directory")
	output := flag.String("output", "", "report path")
	flag.Parse()

	if *input == "" || *output == "" {
		fmt.Fprintln(os.Stderr, "usage: raftreport --input DIR --output FILE")
		os.Exit(2)
	}

	if err := replay.Run(*input, *output); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
