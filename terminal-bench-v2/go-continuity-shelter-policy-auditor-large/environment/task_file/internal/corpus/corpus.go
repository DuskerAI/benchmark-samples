package corpus

import (
    "io/fs"
    "path/filepath"
    "strings"
)

func CountMarkdown(root string) (int, error) {
    count := 0
    err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
        if err != nil { return err }
        if d.IsDir() { return nil }
        if strings.HasSuffix(strings.ToLower(path), ".md") { count++ }
        return nil
    })
    return count, err
}
