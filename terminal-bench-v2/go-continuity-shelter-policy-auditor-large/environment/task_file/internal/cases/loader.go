package cases

import (
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"
    "sort"
    "strings"

    "continuity-audit/internal/model"
)

func LoadConfig(path string) (model.Config, error) {
    var cfg model.Config
    b, err := os.ReadFile(path)
    if err != nil {
        return cfg, err
    }
    if err := json.Unmarshal(b, &cfg); err != nil {
        return cfg, err
    }
    return cfg, nil
}

func LoadCases(root string) ([]model.Case, error) {
    var paths []string
    err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
        if err != nil { return err }
        if d.IsDir() { return nil }
        if strings.HasSuffix(path, ".json") { paths = append(paths, path) }
        return nil
    })
    if err != nil { return nil, err }
    sort.Strings(paths)
    out := make([]model.Case, 0, len(paths))
    for _, p := range paths {
        var c model.Case
        b, err := os.ReadFile(p)
        if err != nil { return nil, err }
        if err := json.Unmarshal(b, &c); err != nil { return nil, fmt.Errorf("%s: %w", p, err) }
        out = append(out, c)
    }
    return out, nil
}
