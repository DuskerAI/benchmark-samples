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
    applied := []string{"GEN_BASE", "WATER_DEFAULT", "STAFFING", "PET_STANDARD"}
    if genRank(c.GeneratorClass) < 1 { reasons = append(reasons, "GENERATOR_CLASS_TOO_LOW") }
    if c.MedicalDependent {
        applied = append(applied, "GEN_MEDICAL")
        if genRank(c.GeneratorClass) < 2 { reasons = append(reasons, "GENERATOR_CLASS_TOO_LOW") }
    }
    if c.WaterDays < 3 { reasons = append(reasons, "WATER_RESERVE_TOO_LOW") }
    maxRatio := 32.0
    if c.MedicalDependent { maxRatio = 26.0 }
    if c.StaffCount <= 0 || float64(c.Residents)/float64(c.StaffCount) > maxRatio { reasons = append(reasons, "STAFFING_RATIO_TOO_HIGH") }
    if c.PetCount > 0 && !c.PetAccommodation { reasons = append(reasons, "PET_ACCOMMODATION_MISSING") }
    if c.EvacuationStatus == "mandatory" {
        applied = append(applied, "TRANSPORT_MANDATORY")
        if c.TransportStatus != "confirmed" { reasons = append(reasons, "TRANSPORT_NOT_CONFIRMED") }
    }
    rows := exceptionRows(cfg, c, reasons)
    decision := "approve"
    if len(reasons) > 0 {
        if allAccepted(rows) { decision = "conditional" } else { decision = "deny" }
    }
    sort.Strings(reasons); sort.Strings(applied)
    return model.Decision{CaseID: c.CaseID, Jurisdiction: c.Jurisdiction, Decision: decision, Reasons: reasons, AppliedRules: unique(applied)}, rows
}

func exceptionRows(cfg model.Config, c model.Case, reasons []string) []model.ExceptionRow {
    reasonSet := map[string]bool{}
    for _, r := range reasons { reasonSet[r] = true }
    rows := make([]model.ExceptionRow, 0, len(c.Exceptions))
    for _, e := range c.Exceptions {
        accepted := active(cfg.EvaluationDate, c.OpenedAt, e.ExpiresOn) && (reasonSet["WATER_RESERVE_TOO_LOW"] || reasonSet["STAFFING_RATIO_TOO_HIGH"])
        rows = append(rows, model.ExceptionRow{CaseID: c.CaseID, ExceptionCode: e.Code, Source: e.Source, ExpiresOn: e.ExpiresOn, Accepted: accepted})
    }
    return rows
}

func allAccepted(rows []model.ExceptionRow) bool {
    if len(rows) == 0 { return false }
    for _, r := range rows { if !r.Accepted { return false } }
    return true
}

func active(evalDate, openedAt, expires string) bool {
    if expires == "" { return false }
    o, err1 := time.Parse("2006-01-02", openedAt)
    x, err2 := time.Parse("2006-01-02", expires)
    if err1 != nil || err2 != nil { return false }
    return !o.After(x)
}

func genRank(s string) int { switch s { case "A": return 3; case "B": return 2; case "C": return 1; default: return 0 } }
func unique(in []string) []string { sort.Strings(in); out := []string{}; for _, v := range in { if len(out)==0 || out[len(out)-1]!=v { out=append(out,v) } }; return out }
