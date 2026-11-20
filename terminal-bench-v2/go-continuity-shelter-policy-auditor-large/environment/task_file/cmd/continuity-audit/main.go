package main

import (
    "flag"
    "fmt"
    "os"

    "continuity-audit/internal/cases"
    "continuity-audit/internal/corpus"
    "continuity-audit/internal/engine"
    "continuity-audit/internal/report"
)

func main() {
    policyRoot := flag.String("policy-root", "", "policy corpus root")
    caseRoot := flag.String("case-root", "", "case root")
    configPath := flag.String("config", "", "config path")
    outDir := flag.String("out-dir", "", "output directory")
    flag.Parse()
    if *policyRoot == "" || *caseRoot == "" || *configPath == "" || *outDir == "" {
        fmt.Fprintln(os.Stderr, "usage: continuity-audit --policy-root DIR --case-root DIR --config FILE --out-dir DIR")
        os.Exit(2)
    }

    cfg, err := cases.LoadConfig(*configPath)
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(1)
    }
    loadedCases, err := cases.LoadCases(*caseRoot)
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(1)
    }
    docCount, err := corpus.CountMarkdown(*policyRoot)
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(1)
    }
    result := engine.Evaluate(cfg, docCount, loadedCases)
    if err := report.WriteAll(*outDir, result); err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(1)
    }
}
