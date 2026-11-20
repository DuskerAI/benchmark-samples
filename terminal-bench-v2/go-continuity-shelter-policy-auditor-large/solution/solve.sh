#!/usr/bin/env bash
set -euo pipefail
cd /app/task_file
cat > internal/engine/engine.go <<'EOF'
package engine

import (
    "sort"
    "time"

    "continuity-audit/internal/model"
)

var baseRules = []model.EffectiveRule{
    {ID: "ACCESS_DELTA_LARGE", Value: "delta shelters over 80 residents require accessibility retrofit", Source: "annexes/delta-accessibility.md", Priority: 70},
    {ID: "GEN_BASE", Value: "minimum generator class C", Source: "handbook/operations-01.md", Priority: 10},
    {ID: "GEN_MEDICAL", Value: "medical dependency requires generator class B", Source: "amendments/2025-11-medical-power.md", Priority: 50},
    {ID: "GEN_RECENCY", Value: "generator test must be within seven days before opening", Source: "bulletins/generator-recency.md", Priority: 72},
    {ID: "GEN_RIDGE_MANDATORY", Value: "ridge mandatory evacuation requires generator class A", Source: "errata/2026-01-ridge-generator.md", Priority: 90},
    {ID: "PET_HARBOR_ADVISORY", Value: "harbor advisory cases may colocate pets without a pet wing", Source: "memos/harbor-advisory-pets.md", Priority: 80},
    {ID: "PET_STANDARD", Value: "pet cases require pet accommodation", Source: "handbook/operations-07.md", Priority: 10},
    {ID: "STAFFING", Value: "default 32 residents per staff, 26 medical, 24 mandatory", Source: "amendments/2025-12-staffing.md", Priority: 60},
    {ID: "TRANSPORT_MANDATORY", Value: "mandatory evacuation requires confirmed transport unless narrow pending exception applies", Source: "annexes/transport-window.md", Priority: 65},
    {ID: "WATER_DEFAULT", Value: "minimum three days water reserve", Source: "handbook/operations-03.md", Priority: 10},
    {ID: "WATER_ISLAND", Value: "island jurisdiction requires five days water reserve", Source: "amendments/2026-02-island-water.md", Priority: 75},
}

func Evaluate(cfg model.Config, docCount int, cases []model.Case) model.Result {
    rules := append([]model.EffectiveRule(nil), baseRules...)
    sort.Slice(rules, func(i, j int) bool { return rules[i].ID < rules[j].ID })
    decisions := make([]model.Decision, 0, len(cases))
    exceptions := make([]model.ExceptionRow, 0)
    for _, c := range cases {
        d, rows := evaluateCase(cfg, c)
        decisions = append(decisions, d)
        exceptions = append(exceptions, rows...)
    }
    sort.Slice(decisions, func(i, j int) bool { return decisions[i].CaseID < decisions[j].CaseID })
    sort.Slice(exceptions, func(i, j int) bool {
        if exceptions[i].CaseID == exceptions[j].CaseID { return exceptions[i].ExceptionCode < exceptions[j].ExceptionCode }
        return exceptions[i].CaseID < exceptions[j].CaseID
    })
    return model.Result{EffectiveRules: model.EffectiveRules{SchemaVersion: 1, PolicyDocuments: docCount, Rules: rules}, DecisionReport: model.DecisionReport{SchemaVersion: 1, CaseCount: len(cases), Decisions: decisions}, Exceptions: exceptions}
}

func evaluateCase(cfg model.Config, c model.Case) (model.Decision, []model.ExceptionRow) {
    reasons := []string{}
    applied := []string{"GEN_BASE", "GEN_RECENCY", "WATER_DEFAULT", "STAFFING", "PET_STANDARD"}
    minGen := 1
    if c.MedicalDependent { minGen = maxInt(minGen, 2); applied = append(applied, "GEN_MEDICAL") }
    if c.Jurisdiction == "ridge" && c.EvacuationStatus == "mandatory" { minGen = maxInt(minGen, 3); applied = append(applied, "GEN_RIDGE_MANDATORY") }
    if genRank(c.GeneratorClass) < minGen { reasons = append(reasons, "GENERATOR_CLASS_TOO_LOW") }
    if generatorStale(c.OpenedAt, c.GeneratorTestedAt) { reasons = append(reasons, "GENERATOR_TEST_STALE") }
    waterMin := 3
    if c.Jurisdiction == "island" && c.OpenedAt >= "2026-02-01" { waterMin = 5; applied = append(applied, "WATER_ISLAND") }
    if c.WaterDays < waterMin { reasons = append(reasons, "WATER_RESERVE_TOO_LOW") }
    maxRatio := 32.0
    if c.MedicalDependent { maxRatio = minFloat(maxRatio, 26.0) }
    if c.EvacuationStatus == "mandatory" { maxRatio = minFloat(maxRatio, 24.0) }
    ratio := 1e12
    if c.StaffCount > 0 { ratio = float64(c.Residents) / float64(c.StaffCount) }
    if ratio > maxRatio { reasons = append(reasons, "STAFFING_RATIO_TOO_HIGH") }
    if c.PetCount > 0 && !c.PetAccommodation {
        if c.Jurisdiction == "harbor" && c.EvacuationStatus == "advisory" { applied = append(applied, "PET_HARBOR_ADVISORY") } else { reasons = append(reasons, "PET_ACCOMMODATION_MISSING") }
    }
    if c.EvacuationStatus == "mandatory" {
        applied = append(applied, "TRANSPORT_MANDATORY")
        pendingOK := c.TransportStatus == "pending" && genRank(c.GeneratorClass) >= 3 && c.WaterDays >= 5 && !c.MedicalDependent
        if c.TransportStatus != "confirmed" && !pendingOK { reasons = append(reasons, "TRANSPORT_NOT_CONFIRMED") }
    }
    if c.MedicalDependent && !c.AccessibilityRetrofit { reasons = append(reasons, "ACCESSIBILITY_RETROFIT_REQUIRED") }
    if c.Jurisdiction == "delta" && c.Residents > 80 {
        applied = append(applied, "ACCESS_DELTA_LARGE")
        if !c.AccessibilityRetrofit { reasons = append(reasons, "ACCESSIBILITY_RETROFIT_REQUIRED") }
    }
    rows, accepted := exceptionRows(cfg, c, reasons, waterMin, maxRatio, ratio)
    unresolved := []string{}
    for _, r := range reasons { if !accepted[r] { unresolved = append(unresolved, r) } }
    decision := "approve"
    if len(reasons) > 0 { if len(unresolved) == 0 { decision = "conditional" } else { decision = "deny" } }
    sort.Strings(reasons); sort.Strings(applied)
    return model.Decision{CaseID: c.CaseID, Jurisdiction: c.Jurisdiction, Decision: decision, Reasons: unique(reasons), AppliedRules: unique(applied)}, rows
}

func exceptionRows(cfg model.Config, c model.Case, reasons []string, waterMin int, maxRatio, ratio float64) ([]model.ExceptionRow, map[string]bool) {
    reasonSet := map[string]bool{}; for _, r := range reasons { reasonSet[r] = true }
    accepted := map[string]bool{}
    rows := make([]model.ExceptionRow, 0, len(c.Exceptions))
    for _, e := range c.Exceptions {
        ok := false
        if active(cfg.EvaluationDate, c.OpenedAt, e.ExpiresOn) {
            if e.Code == "WATER_SHORTAGE_1D" && reasonSet["WATER_RESERVE_TOO_LOW"] && c.WaterDays >= waterMin-1 { ok = true; accepted["WATER_RESERVE_TOO_LOW"] = true }
            if e.Code == "STAFFING_10_PERCENT" && reasonSet["STAFFING_RATIO_TOO_HIGH"] && ratio <= maxRatio*1.10 { ok = true; accepted["STAFFING_RATIO_TOO_HIGH"] = true }
        }
        rows = append(rows, model.ExceptionRow{CaseID: c.CaseID, ExceptionCode: e.Code, Source: e.Source, ExpiresOn: e.ExpiresOn, Accepted: ok})
    }
    return rows, accepted
}

func active(evalDate, openedAt, expires string) bool {
    if expires == "" { return false }
    o, err1 := time.Parse("2006-01-02", openedAt)
    x, err2 := time.Parse("2006-01-02", expires)
    if err1 != nil || err2 != nil { return false }
    return !o.After(x)
}

func generatorStale(openedAt, testedAt string) bool {
    opened, err1 := time.Parse("2006-01-02", openedAt)
    tested, err2 := time.Parse("2006-01-02", testedAt)
    if err1 != nil || err2 != nil { return true }
    if tested.After(opened) { return true }
    return opened.Sub(tested) > 7*24*time.Hour
}

func genRank(s string) int { switch s { case "A": return 3; case "B": return 2; case "C": return 1; default: return 0 } }
func unique(in []string) []string { sort.Strings(in); out := []string{}; for _, v := range in { if len(out)==0 || out[len(out)-1]!=v { out=append(out,v) } }; return out }
func maxInt(a,b int) int { if a>b { return a }; return b }
func minFloat(a,b float64) float64 { if a<b { return a }; return b }
EOF
go build -o /app/task_file/bin/continuity-audit /app/task_file/cmd/continuity-audit
/app/task_file/bin/continuity-audit --policy-root /opt/continuity-policy-corpus --case-root /var/lib/continuity-audit/cases --config /etc/continuity-audit/config.json --out-dir /var/lib/continuity-audit/output
test -s /var/lib/continuity-audit/output/effective_rules.json
test -s /var/lib/continuity-audit/output/case_decisions.json
test -s /var/lib/continuity-audit/output/exceptions.csv
