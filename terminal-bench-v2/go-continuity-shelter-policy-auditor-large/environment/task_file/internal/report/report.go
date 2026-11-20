package report

import (
    "encoding/csv"
    "encoding/json"
    "os"
    "path/filepath"
    "strconv"

    "continuity-audit/internal/model"
)

func WriteAll(outDir string, result model.Result) error {
    if err := os.MkdirAll(outDir, 0755); err != nil { return err }
    if err := writeJSON(filepath.Join(outDir, "effective_rules.json"), result.EffectiveRules); err != nil { return err }
    if err := writeJSON(filepath.Join(outDir, "case_decisions.json"), result.DecisionReport); err != nil { return err }
    return writeExceptions(filepath.Join(outDir, "exceptions.csv"), result.Exceptions)
}

func writeJSON(path string, v any) error {
    b, err := json.MarshalIndent(v, "", "  ")
    if err != nil { return err }
    b = append(b, '\n')
    return os.WriteFile(path, b, 0644)
}

func writeExceptions(path string, rows []model.ExceptionRow) error {
    f, err := os.Create(path)
    if err != nil { return err }
    defer f.Close()
    w := csv.NewWriter(f)
    if err := w.Write([]string{"case_id", "exception_code", "source", "expires_on", "accepted"}); err != nil { return err }
    for _, r := range rows {
        if err := w.Write([]string{r.CaseID, r.ExceptionCode, r.Source, r.ExpiresOn, strconv.FormatBool(r.Accepted)}); err != nil { return err }
    }
    w.Flush()
    return w.Error()
}
