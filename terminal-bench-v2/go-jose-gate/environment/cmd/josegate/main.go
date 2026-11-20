package main

import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/base64"
    "encoding/json"
    "flag"
    "fmt"
    "os"
    "path/filepath"
    "sort"
)

type keyEntry struct {
    Kid string `json:"kid"`
    Kty string `json:"kty"`
    Alg string `json:"alg"`
    Use string `json:"use"`
    K   string `json:"k"`
}

type keySet struct { Keys []keyEntry `json:"keys"` }
type token struct { ID, Protected, Payload, Signature string }
type accepted struct { ID, Subject, Kid string; Seq int; Sha256 string }
type rejected struct { ID, Reason string }

func main() {
    keysPath := flag.String("keys", "", "")
    tokensDir := flag.String("tokens", "", "")
    statePath := flag.String("state", "", "")
    outPath := flag.String("out", "", "")
    now := flag.Int64("now", 0, "")
    flag.Parse()
    _ = statePath

    keys, err := loadKeys(*keysPath)
    if err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(2) }
    paths, err := filepath.Glob(filepath.Join(*tokensDir, "*.json"))
    if err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(2) }
    sort.Strings(paths)

    var acc []map[string]any
    var rej []map[string]any
    for _, p := range paths {
        raw, err := os.ReadFile(p)
        if err != nil { continue }
        var t token
        if json.Unmarshal(raw, &t) != nil { continue }
        hdrBytes, err1 := base64.RawURLEncoding.DecodeString(t.Protected)
        plBytes, err2 := base64.RawURLEncoding.DecodeString(t.Payload)
        if err1 != nil || err2 != nil { rej = append(rej, map[string]any{"id": t.ID, "reason": "bad_encoding"}); continue }
        var hdr map[string]any
        var claim map[string]any
        if json.Unmarshal(hdrBytes, &hdr) != nil || json.Unmarshal(plBytes, &claim) != nil { rej = append(rej, map[string]any{"id": t.ID, "reason": "malformed"}); continue }
        kid, _ := hdr["kid"].(string)
        k, ok := keys[kid]
        if !ok { rej = append(rej, map[string]any{"id": t.ID, "reason": "unknown_kid"}); continue }
        mac := hmac.New(sha256.New, k)
        mac.Write([]byte(t.Protected + "." + t.Payload))
        want := mac.Sum(nil)
        got, err := base64.RawURLEncoding.DecodeString(t.Signature)
        if err != nil || !hmac.Equal(want, got) { rej = append(rej, map[string]any{"id": t.ID, "reason": "bad_signature"}); continue }
        nbf := int64(claim["nbf"].(float64)); exp := int64(claim["exp"].(float64)); eventTime := int64(claim["event_time"].(float64))
        if !(*now >= nbf && *now <= exp && eventTime >= nbf && eventTime <= exp) { rej = append(rej, map[string]any{"id": t.ID, "reason": "claim_window"}); continue }
        seq := int(claim["seq"].(float64))
        acc = append(acc, map[string]any{"id": t.ID, "subject": claim["subject"], "kid": kid, "seq": seq, "sha256": claim["sha256"]})
    }
    report := map[string]any{"accepted": acc, "rejected": rej, "summary": map[string]any{"accepted": len(acc), "rejected": len(rej)}}
    b, _ := json.MarshalIndent(report, "", "  ")
    if err := os.MkdirAll(filepath.Dir(*outPath), 0o755); err != nil { os.Exit(2) }
    if err := os.WriteFile(*outPath, append(b, '\n'), 0o644); err != nil { os.Exit(2) }
}

func loadKeys(path string) (map[string][]byte, error) {
    raw, err := os.ReadFile(path)
    if err != nil { return nil, err }
    var ks keySet
    if err := json.Unmarshal(raw, &ks); err != nil { return nil, err }
    out := map[string][]byte{}
    for _, k := range ks.Keys {
        b, err := base64.RawURLEncoding.DecodeString(k.K)
        if err == nil { out[k.Kid] = b }
    }
    return out, nil
}
