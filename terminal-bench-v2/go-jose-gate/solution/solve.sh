#!/bin/bash
set -euo pipefail

cat > /app/josegate/cmd/josegate/main.go <<'GO'
package main

import (
    "bytes"
    "crypto/hmac"
    "crypto/sha256"
    "encoding/base64"
    "encoding/json"
    "errors"
    "flag"
    "fmt"
    "io"
    "os"
    "path/filepath"
    "sort"
    "strconv"
    "strings"
)

type jwk struct {
    Kid string `json:"kid"`
    Kty string `json:"kty"`
    Alg string `json:"alg"`
    Use string `json:"use"`
    K   string `json:"k"`
    Nbf *int64 `json:"nbf"`
    Exp *int64 `json:"exp"`
}

type keySet struct {
    Keys []jwk `json:"keys"`
}

type tokenFile struct {
    ID        string `json:"id"`
    Protected string `json:"protected"`
    Payload   string `json:"payload"`
    Signature string `json:"signature"`
}

type stateFile struct {
    Accepted []stateRecord `json:"accepted"`
}

type stateRecord struct {
    Kid     string `json:"kid"`
    Nonce   string `json:"nonce"`
    Subject string `json:"subject"`
    Seq     int64  `json:"seq"`
    Sha256  string `json:"sha256"`
}

type acceptedRow struct {
    ID      string `json:"id"`
    Subject string `json:"subject"`
    Kid     string `json:"kid"`
    Seq     int64  `json:"seq"`
    Sha256  string `json:"sha256"`
}

type rejectedRow struct {
    ID     string `json:"id"`
    Reason string `json:"reason"`
}

type report struct {
    Accepted []acceptedRow `json:"accepted"`
    Rejected []rejectedRow `json:"rejected"`
    Summary  summary       `json:"summary"`
}

type summary struct {
    Accepted int `json:"accepted"`
    Rejected int `json:"rejected"`
}

type subjectState struct {
    Seq    int64
    Digest string
}

type keyInfo struct {
    Secret []byte
    Nbf    int64
    Exp    int64
}

type segmentClaim struct {
    Offset int64
    Length int64
    Sha256 string
}

type claimFields struct {
    Subject      string
    Nonce        string
    Artifact     string
    Sha256       string
    Previous     *string
    Segments     []segmentClaim
    Seq          int64
    Nbf          int64
    Exp          int64
    EventTime    int64
    ArtifactSize int64
}

var rowNonce = map[string]string{}

func main() {
    keysPath := flag.String("keys", "", "path to JWK keyset")
    tokensDir := flag.String("tokens", "", "directory of token JSON files")
    statePath := flag.String("state", "", "replay state JSON")
    outPath := flag.String("out", "", "report path")
    now := flag.Int64("now", 0, "audit time")
    flag.Parse()

    if *keysPath == "" || *tokensDir == "" || *statePath == "" || *outPath == "" {
        fmt.Fprintln(os.Stderr, "missing required argument")
        os.Exit(2)
    }

    usable, err := loadUsableKeys(*keysPath)
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
    nonces, subjects, err := loadReplayState(*statePath)
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
    paths, err := filepath.Glob(filepath.Join(*tokensDir, "*.json"))
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
    sort.Slice(paths, func(i, j int) bool { return paths[i] < paths[j] })

    accepted := make([]acceptedRow, 0)
    rejected := make([]rejectedRow, 0)
    for _, path := range paths {
        row, reason := auditToken(
            path,
            filepath.Join(filepath.Dir(*tokensDir), "artifacts"),
            usable,
            nonces,
            subjects,
            *now,
        )
        if reason != "" {
            rejected = append(rejected, rejectedRow{ID: row.ID, Reason: reason})
            continue
        }
        accepted = append(accepted, row)
        claimNonce := rowNonce[path]
        nonces[row.Kid+"\x00"+claimNonce] = true
        subjects[row.Subject] = subjectState{Seq: row.Seq, Digest: row.Sha256}
        delete(rowNonce, path)
    }

    out := report{
        Accepted: accepted,
        Rejected: rejected,
        Summary:  summary{Accepted: len(accepted), Rejected: len(rejected)},
    }
    data, err := json.MarshalIndent(out, "", "  ")
    if err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
    if err := os.MkdirAll(filepath.Dir(*outPath), 0o755); err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
    if err := os.WriteFile(*outPath, append(data, '\n'), 0o644); err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(2)
    }
}

func loadUsableKeys(path string) (map[string]keyInfo, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, err
    }
    var ks keySet
    if err := json.Unmarshal(data, &ks); err != nil {
        return nil, err
    }
    counts := map[string]int{}
    for _, key := range ks.Keys {
        counts[key.Kid]++
    }
    out := map[string]keyInfo{}
    for _, key := range ks.Keys {
        if key.Kid == "" || counts[key.Kid] != 1 || key.Kty != "oct" || key.Alg != "HS256" || key.Use != "sig" {
            continue
        }
        if key.Nbf == nil || key.Exp == nil || *key.Nbf > *key.Exp {
            continue
        }
        raw, err := base64.RawURLEncoding.DecodeString(key.K)
        if err != nil {
            continue
        }
        out[key.Kid] = keyInfo{Secret: raw, Nbf: *key.Nbf, Exp: *key.Exp}
    }
    return out, nil
}

func loadReplayState(path string) (map[string]bool, map[string]subjectState, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, nil, err
    }
    var st stateFile
    if err := json.Unmarshal(data, &st); err != nil {
        return nil, nil, err
    }
    nonces := map[string]bool{}
    subjects := map[string]subjectState{}
    for _, rec := range st.Accepted {
        nonces[rec.Kid+"\x00"+rec.Nonce] = true
        cur, ok := subjects[rec.Subject]
        if !ok || rec.Seq > cur.Seq {
            subjects[rec.Subject] = subjectState{Seq: rec.Seq, Digest: rec.Sha256}
        }
    }
    return nonces, subjects, nil
}

func auditToken(
    path string,
    artifactsRoot string,
    keys map[string]keyInfo,
    nonces map[string]bool,
    subjects map[string]subjectState,
    now int64,
) (acceptedRow, string) {
    data, err := os.ReadFile(path)
    if err != nil {
        return acceptedRow{}, "malformed"
    }
    var tok tokenFile
    if err := json.Unmarshal(data, &tok); err != nil || tok.ID == "" || tok.Protected == "" || tok.Payload == "" || tok.Signature == "" {
        return acceptedRow{ID: tok.ID}, "malformed"
    }
    headerBytes, err1 := base64.RawURLEncoding.DecodeString(tok.Protected)
    payloadBytes, err2 := base64.RawURLEncoding.DecodeString(tok.Payload)
    sig, err3 := base64.RawURLEncoding.DecodeString(tok.Signature)
    if err1 != nil || err2 != nil || err3 != nil || len(sig) != sha256.Size {
        return acceptedRow{ID: tok.ID}, "bad_encoding"
    }
    header, err := decodeJSON(headerBytes)
    if err != nil {
        return acceptedRow{ID: tok.ID}, "malformed"
    }
    claim, err := decodeJSON(payloadBytes)
    if err != nil {
        return acceptedRow{ID: tok.ID}, "malformed"
    }
    headerCanon, err := canonicalJSON(header)
    if err != nil {
        return acceptedRow{ID: tok.ID}, "malformed"
    }
    claimCanon, err := canonicalJSON(claim)
    if err != nil {
        return acceptedRow{ID: tok.ID}, "malformed"
    }
    if base64.RawURLEncoding.EncodeToString(headerCanon) != tok.Protected || base64.RawURLEncoding.EncodeToString(claimCanon) != tok.Payload {
        return acceptedRow{ID: tok.ID}, "noncanonical"
    }
    kid, ok := header["kid"].(string)
    if !ok || !headerExact(header) {
        return acceptedRow{ID: tok.ID}, "bad_header"
    }
    key, ok := keys[kid]
    if !ok {
        return acceptedRow{ID: tok.ID}, "unknown_kid"
    }
    mac := hmac.New(sha256.New, key.Secret)
    _, _ = mac.Write([]byte(tok.Protected + "." + tok.Payload))
    if !hmac.Equal(mac.Sum(nil), sig) {
        return acceptedRow{ID: tok.ID}, "bad_signature"
    }
    parsed, ok := claimFromMap(claim, tok.ID)
    if !ok {
        return acceptedRow{ID: tok.ID}, "malformed"
    }
    if !(parsed.Nbf <= now && now <= parsed.Exp && parsed.Nbf <= parsed.EventTime && parsed.EventTime <= parsed.Exp && parsed.Exp-parsed.Nbf <= 86400) {
        return acceptedRow{ID: tok.ID}, "claim_window"
    }
    if !(key.Nbf <= parsed.EventTime && parsed.EventTime <= key.Exp) {
        return acceptedRow{ID: tok.ID}, "key_window"
    }
    if !validArtifactPath(parsed.Artifact) {
        return acceptedRow{ID: tok.ID}, "artifact_path"
    }
    artifactData, err := os.ReadFile(filepath.Join(artifactsRoot, filepath.FromSlash(parsed.Artifact)))
    if err != nil {
        return acceptedRow{ID: tok.ID}, "artifact_path"
    }
    artifactDigest := sha256.Sum256(artifactData)
    if fmt.Sprintf("%x", artifactDigest[:]) != parsed.Sha256 || !segmentsValid(artifactData, parsed) {
        return acceptedRow{ID: tok.ID}, "artifact_digest"
    }
    if nonces[kid+"\x00"+parsed.Nonce] {
        return acceptedRow{ID: tok.ID}, "replay"
    }
    prior, exists := subjects[parsed.Subject]
    if exists && parsed.Seq <= prior.Seq {
        return acceptedRow{ID: tok.ID}, "sequence"
    }
    requiredPrevious := ""
    if exists {
        requiredPrevious = prior.Digest
    }
    if (!exists && parsed.Previous != nil) || (exists && (parsed.Previous == nil || *parsed.Previous != requiredPrevious)) {
        return acceptedRow{ID: tok.ID}, "chain"
    }
    rowNonce[path] = parsed.Nonce
    return acceptedRow{
        ID:      tok.ID,
        Subject: parsed.Subject,
        Kid:     kid,
        Seq:     parsed.Seq,
        Sha256:  parsed.Sha256,
    }, ""
}

func headerExact(header map[string]any) bool {
    if len(header) != 5 {
        return false
    }
    crit, ok := header["crit"].([]any)
    if !ok || len(crit) != 1 || crit[0] != "josegate-c14n" {
        return false
    }
    return header["alg"] == "HS256" && header["typ"] == "evidence+jws" && header["cty"] == "application/josegate+json"
}

func claimFromMap(m map[string]any, tokenID string) (claimFields, bool) {
    if s, ok := m["evidence_id"].(string); !ok || s != tokenID {
        return claimFields{}, false
    }
    subject, ok1 := m["subject"].(string)
    nonce, ok2 := m["nonce"].(string)
    artifact, ok3 := m["artifact"].(string)
    fullDigest, ok4 := m["sha256"].(string)
    if !ok1 || !ok2 || !ok3 || !ok4 || !isLowerHex64(fullDigest) {
        return claimFields{}, false
    }
    var previous *string
    if m["previous"] != nil {
        p, ok := m["previous"].(string)
        if !ok || !isLowerHex64(p) {
            return claimFields{}, false
        }
        previous = &p
    }
    seq, ok1 := intFrom(m["seq"])
    nbf, ok2 := intFrom(m["nbf"])
    exp, ok3 := intFrom(m["exp"])
    eventTime, ok4 := intFrom(m["event_time"])
    artifactSize, ok5 := intFrom(m["artifact_size"])
    if !ok1 || !ok2 || !ok3 || !ok4 || !ok5 || artifactSize < 0 {
        return claimFields{}, false
    }
    segments, ok := segmentsFrom(m["segments"])
    if !ok {
        return claimFields{}, false
    }
    return claimFields{
        Subject:      subject,
        Nonce:        nonce,
        Artifact:     artifact,
        Sha256:       fullDigest,
        Previous:     previous,
        Segments:     segments,
        Seq:          seq,
        Nbf:          nbf,
        Exp:          exp,
        EventTime:    eventTime,
        ArtifactSize: artifactSize,
    }, true
}

func segmentsFrom(v any) ([]segmentClaim, bool) {
    raw, ok := v.([]any)
    if !ok || len(raw) == 0 {
        return nil, false
    }
    out := make([]segmentClaim, 0, len(raw))
    for _, item := range raw {
        m, ok := item.(map[string]any)
        if !ok || len(m) != 3 {
            return nil, false
        }
        offset, ok1 := intFrom(m["offset"])
        length, ok2 := intFrom(m["length"])
        digest, ok3 := m["sha256"].(string)
        if !ok1 || !ok2 || !ok3 || !isLowerHex64(digest) {
            return nil, false
        }
        out = append(out, segmentClaim{Offset: offset, Length: length, Sha256: digest})
    }
    return out, true
}

func validArtifactPath(s string) bool {
    if s == "" || strings.HasPrefix(s, "/") || strings.Contains(s, "\\") {
        return false
    }
    for _, part := range strings.Split(s, "/") {
        if part == "" || part == "." || part == ".." {
            return false
        }
    }
    return true
}

func segmentsValid(data []byte, claim claimFields) bool {
    if claim.ArtifactSize != int64(len(data)) {
        return false
    }
    var offset int64
    for _, segment := range claim.Segments {
        if segment.Offset != offset || segment.Length <= 0 {
            return false
        }
        end := offset + segment.Length
        if end > int64(len(data)) {
            return false
        }
        partDigest := sha256.Sum256(data[offset:end])
        if fmt.Sprintf("%x", partDigest[:]) != segment.Sha256 {
            return false
        }
        offset = end
    }
    return offset == int64(len(data))
}

func intFrom(v any) (int64, bool) {
    n, ok := v.(json.Number)
    if !ok || strings.ContainsAny(n.String(), ".eE") {
        return 0, false
    }
    i, err := strconv.ParseInt(n.String(), 10, 64)
    return i, err == nil
}

func isLowerHex64(s string) bool {
    if len(s) != 64 {
        return false
    }
    for _, ch := range s {
        if !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f')) {
            return false
        }
    }
    return true
}

func decodeJSON(data []byte) (map[string]any, error) {
    dec := json.NewDecoder(bytes.NewReader(data))
    dec.UseNumber()
    var value map[string]any
    if err := dec.Decode(&value); err != nil {
        return nil, err
    }
    if dec.Decode(&struct{}{}) != io.EOF {
        return nil, errors.New("trailing json")
    }
    return value, nil
}

func canonicalJSON(v any) ([]byte, error) {
    var b strings.Builder
    if err := writeCanonical(&b, v); err != nil {
        return nil, err
    }
    return []byte(b.String()), nil
}

func writeCanonical(b *strings.Builder, v any) error {
    switch x := v.(type) {
    case nil:
        b.WriteString("null")
    case bool:
        if x {
            b.WriteString("true")
        } else {
            b.WriteString("false")
        }
    case string:
        b.WriteString(strconv.Quote(x))
    case json.Number:
        s := x.String()
        if strings.ContainsAny(s, ".eE") {
            return errors.New("non-integer number")
        }
        if _, err := strconv.ParseInt(s, 10, 64); err != nil {
            return err
        }
        b.WriteString(s)
    case []any:
        b.WriteByte('[')
        for i, item := range x {
            if i > 0 {
                b.WriteByte(',')
            }
            if err := writeCanonical(b, item); err != nil {
                return err
            }
        }
        b.WriteByte(']')
    case map[string]any:
        keys := make([]string, 0, len(x))
        for k := range x {
            keys = append(keys, k)
        }
        sort.Strings(keys)
        b.WriteByte('{')
        for i, k := range keys {
            if i > 0 {
                b.WriteByte(',')
            }
            b.WriteString(strconv.Quote(k))
            b.WriteByte(':')
            if err := writeCanonical(b, x[k]); err != nil {
                return err
            }
        }
        b.WriteByte('}')
    default:
        return fmt.Errorf("unsupported JSON value %T", v)
    }
    return nil
}
GO

gofmt -w /app/josegate/cmd/josegate/main.go
/app/josegate/scripts/build.sh
