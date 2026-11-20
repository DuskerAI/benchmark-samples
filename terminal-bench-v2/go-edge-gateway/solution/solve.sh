#!/bin/bash
set -euo pipefail

mkdir -p /app/config /app/signals /app/systemd /app/bin
mkdir -p /app/systemd/firewall.service.d /app/systemd/gateway.service.d

cat > /app/cmd/gatewayctl/main.go <<'GO'
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type Manifest struct { ProfileVersion string `json:"profile_version"`; Services []Service `json:"services"` }
type Service struct { Name string `json:"name"`; After []string `json:"after"`; Env map[string]string `json:"env"`; HealthPath string `json:"health_path"`; Restart string `json:"restart"` }
type Status struct { Generation int `json:"generation"`; LastGoodGeneration int `json:"last_good_generation"`; ProfileVersion string `json:"profile_version"`; Fingerprint string `json:"fingerprint"`; Services []StatusService `json:"services"` }
type StatusService struct { Name string `json:"name"`; State string `json:"state"`; Config string `json:"config"`; Unit string `json:"unit"`; After []string `json:"after"`; DependencyClosure []string `json:"dependency_closure"`; Restart string `json:"restart"` }
type Activation struct { ProfileVersion string `json:"profile_version"`; Services []ActivationService `json:"services"` }
type ActivationService struct { Name string `json:"name"`; After []string `json:"after"`; DependencyClosure []string `json:"dependency_closure"`; Restart string `json:"restart"`; HealthPath string `json:"health_path,omitempty"`; ConfigSHA256 string `json:"config_sha256"` }
type HistoryEntry struct { Generation int `json:"generation"`; ProfileVersion string `json:"profile_version"`; Fingerprint string `json:"fingerprint"`; Services []string `json:"services"` }

var serviceNameRe = regexp.MustCompile(`^[a-z0-9-]+$`)
var envKeyRe = regexp.MustCompile(`^[A-Z][A-Z0-9_]*$`)
var refRe = regexp.MustCompile(`\$\{([a-z0-9-]+)\.([A-Z][A-Z0-9_]*)\}`)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "apply" { fmt.Fprintln(os.Stderr, "usage: gatewayctl apply --manifest PATH --state DIR --out DIR"); os.Exit(2) }
	fs := flag.NewFlagSet("apply", flag.ExitOnError); manifestPath := fs.String("manifest", "/app/config/site.json", "manifest path"); stateDir := fs.String("state", "/app/run/state", "state dir"); outDir := fs.String("out", "/app/run/rendered", "output dir"); _ = fs.Parse(os.Args[2:])
	if err := apply(*manifestPath, *stateDir, *outDir); err != nil { _ = os.MkdirAll(*stateDir, 0755); _ = writeJSONAtomic(filepath.Join(*stateDir, "last_error.json"), map[string]string{"error": err.Error()}); fmt.Fprintln(os.Stderr, err); os.Exit(1) }
}

func apply(manifestPath, stateDir, outDir string) error {
	manifest, err := readManifest(manifestPath); if err != nil { return err }
	order, err := activationOrder(manifest.Services); if err != nil { return err }
	expanded, err := expandAll(order); if err != nil { return err }
	closures := dependencyClosures(order)
	if err := validateUnits(order); err != nil { return err }
	if err := checkHealth(order); err != nil { return err }
	parent := filepath.Dir(outDir); if err := os.MkdirAll(parent, 0755); err != nil { return err }
	stage, err := os.MkdirTemp(parent, ".stage-"); if err != nil { return err }; defer os.RemoveAll(stage)
	if err := render(stage, manifest.ProfileVersion, order, expanded, closures); err != nil { return err }
	fp, err := fingerprint(stage, order); if err != nil { return err }
	previous, _ := readStatus(filepath.Join(stateDir, "status.json")); generation := 1; changedActivation := true
	if previous.Generation > 0 { generation = previous.Generation; changedActivation = previous.Fingerprint != fp; if changedActivation { generation++ } }
	status := Status{Generation:generation, LastGoodGeneration:generation, ProfileVersion:manifest.ProfileVersion, Fingerprint:fp}
	historyNames := []string{}
	for _, svc := range order { historyNames = append(historyNames, svc.Name); status.Services = append(status.Services, StatusService{Name:svc.Name, State:"healthy", Config:filepath.Join(outDir,"configs",svc.Name+".env"), Unit:filepath.Join("/app/systemd",svc.Name+".service"), After:copySlice(svc.After), DependencyClosure:closures[svc.Name], Restart:restartValue(svc.Restart)}) }
	if err := publish(stage, outDir); err != nil { return err }
	if err := os.MkdirAll(stateDir, 0755); err != nil { return err }
	if err := writeJSONAtomic(filepath.Join(stateDir,"status.json"), status); err != nil { return err }
	if changedActivation { if err := appendHistory(filepath.Join(stateDir,"history.jsonl"), HistoryEntry{Generation:generation, ProfileVersion:manifest.ProfileVersion, Fingerprint:fp, Services:historyNames}); err != nil { return err } }
	if err := os.Remove(filepath.Join(stateDir,"last_error.json")); err != nil && !errors.Is(err, fs.ErrNotExist) { return err }
	return nil
}

func readManifest(path string) (Manifest, error) {
	data, err := os.ReadFile(path); if err != nil { return Manifest{}, err }
	var manifest Manifest; if err := json.Unmarshal(data, &manifest); err != nil { return Manifest{}, err }
	if manifest.ProfileVersion == "" { return Manifest{}, errors.New("manifest profile_version is required") }
	if len(manifest.Services) == 0 { return Manifest{}, errors.New("manifest has no services") }
	seen := map[string]bool{}
	for i := range manifest.Services { svc := &manifest.Services[i]; if svc.Env == nil { svc.Env = map[string]string{} }; svc.Restart = restartValue(svc.Restart)
		if !serviceNameRe.MatchString(svc.Name) { return Manifest{}, fmt.Errorf("invalid service name %q", svc.Name) }
		if seen[svc.Name] { return Manifest{}, fmt.Errorf("duplicate service %q", svc.Name) }; seen[svc.Name] = true
		if svc.Restart != "no" && svc.Restart != "on-failure" && svc.Restart != "always" { return Manifest{}, fmt.Errorf("invalid restart for %s", svc.Name) }
		seenDeps := map[string]bool{}; for _, dep := range svc.After { if dep == svc.Name { return Manifest{}, fmt.Errorf("service %s depends on itself", svc.Name) }; if seenDeps[dep] { return Manifest{}, fmt.Errorf("service %s repeats dependency %s", svc.Name, dep) }; seenDeps[dep] = true }
		if svc.HealthPath != "" { clean := filepath.Clean(svc.HealthPath); if !filepath.IsAbs(svc.HealthPath) || clean != svc.HealthPath || !strings.HasPrefix(clean, "/app/signals/") { return Manifest{}, fmt.Errorf("invalid health path for %s", svc.Name) } }
		for key, value := range svc.Env { if !envKeyRe.MatchString(key) { return Manifest{}, fmt.Errorf("invalid env key %q", key) }; if strings.ContainsAny(value, "\n\r") { return Manifest{}, fmt.Errorf("invalid env value for %s", key) } }
	}
	return manifest, nil
}

func restartValue(v string) string { if v == "" { return "no" }; return v }
func copySlice(in []string) []string { out := make([]string, len(in)); copy(out, in); return out }

func activationOrder(services []Service) ([]Service, error) {
	byName := map[string]Service{}; for _, svc := range services { byName[svc.Name] = svc }
	for _, svc := range services { for _, dep := range svc.After { if _, ok := byName[dep]; !ok { return nil, fmt.Errorf("service %s depends on missing service %s", svc.Name, dep) } } }
	done := map[string]bool{}; order := make([]Service,0,len(services))
	for len(order) < len(services) { progress := false; for _, svc := range services { if done[svc.Name] { continue }; ready := true; for _, dep := range svc.After { if !done[dep] { ready=false; break } }; if ready { order=append(order, svc); done[svc.Name]=true; progress=true } }; if !progress { return nil, errors.New("dependency cycle detected") } }
	return order, nil
}

func dependencyClosures(order []Service) map[string][]string {
	seenBySvc := map[string]map[string]bool{}; result := map[string][]string{}
	for _, svc := range order { seen := map[string]bool{}; for _, dep := range svc.After { if depSeen := seenBySvc[dep]; depSeen != nil { for name := range depSeen { seen[name] = true } }; seen[dep] = true }; closure := []string{}; for _, candidate := range order { if seen[candidate.Name] { closure = append(closure, candidate.Name) } }; seenBySvc[svc.Name]=seen; result[svc.Name]=closure }
	return result
}

func expandAll(order []Service) (map[string]map[string]string, error) {
	byName := map[string]Service{}; for _, svc := range order { byName[svc.Name] = svc }
	closures := dependencyClosures(order); closureSet := map[string]map[string]bool{}; for name, closure := range closures { closureSet[name]=map[string]bool{}; for _, dep := range closure { closureSet[name][dep]=true } }
	expanded := map[string]map[string]string{}; visiting := map[string]bool{}
	var expandKey func(string,string) (string,error)
	expandKey = func(svcName, key string) (string,error) { id := svcName+"."+key; if visiting[id] { return "", fmt.Errorf("environment reference cycle at %s", id) }; if expanded[svcName] == nil { expanded[svcName]=map[string]string{} }; if value, ok := expanded[svcName][key]; ok { return value,nil }; svc := byName[svcName]; raw, ok := svc.Env[key]; if !ok { return "", fmt.Errorf("missing env reference %s", id) }; visiting[id]=true; var firstErr error; out := refRe.ReplaceAllStringFunc(raw, func(token string) string { if firstErr != nil { return "" }; parts := refRe.FindStringSubmatch(token); refSvc, refKey := parts[1], parts[2]; if refSvc == svcName || !closureSet[svcName][refSvc] { firstErr = fmt.Errorf("service %s has invalid env reference %s.%s", svcName, refSvc, refKey); return "" }; val, err := expandKey(refSvc, refKey); if err != nil { firstErr = err; return "" }; return val }); visiting[id]=false; if firstErr != nil { return "", firstErr }; expanded[svcName][key]=out; return out,nil }
	for _, svc := range order { for key := range svc.Env { if _, err := expandKey(svc.Name,key); err != nil { return nil, err } }; if expanded[svc.Name] == nil { expanded[svc.Name]=map[string]string{} } }
	return expanded,nil
}

func parseUnitInto(sections map[string]map[string]string, data []byte) { current := ""; for _, raw := range strings.Split(string(data), "\n") { line := strings.TrimSpace(raw); if line == "" || strings.HasPrefix(line,"#") || strings.HasPrefix(line,";") { continue }; if strings.HasPrefix(line,"[") && strings.HasSuffix(line,"]") { current = strings.TrimSuffix(strings.TrimPrefix(line,"["),"]"); if sections[current] == nil { sections[current]=map[string]string{} }; continue }; key,value,ok := strings.Cut(line,"="); if ok && current != "" { sections[current][strings.TrimSpace(key)] = strings.TrimSpace(value) } } }

func unitFiles(name string) ([]string,error) { files := []string{filepath.Join("/app/systemd", name+".service")}; dropins, err := filepath.Glob(filepath.Join("/app/systemd", name+".service.d", "*.conf")); if err != nil { return nil, err }; sort.Strings(dropins); files = append(files, dropins...); return files,nil }

func readEffectiveUnit(name string) (map[string]map[string]string,error) { sections := map[string]map[string]string{}; files, err := unitFiles(name); if err != nil { return nil,err }; for _, path := range files { data, err := os.ReadFile(path); if err != nil { return nil, fmt.Errorf("unit file for %s: %w", name, err) }; parseUnitInto(sections,data) }; return sections,nil }

func validateUnits(order []Service) error { for _, svc := range order { sections, err := readEffectiveUnit(svc.Name); if err != nil { return err }; expectedAfter := ""; if len(svc.After)>0 { parts := []string{}; for _, dep := range svc.After { parts=append(parts,dep+".service") }; expectedAfter = strings.Join(parts," ") }; if strings.TrimSpace(sections["Unit"]["After"]) != expectedAfter { return fmt.Errorf("unit %s After mismatch", svc.Name) }; expectedEnv := filepath.Join("/app/run/rendered/configs", svc.Name+".env"); if strings.TrimSpace(sections["Service"]["EnvironmentFile"]) != expectedEnv { return fmt.Errorf("unit %s EnvironmentFile mismatch", svc.Name) }; if strings.TrimSpace(sections["Service"]["Restart"]) != restartValue(svc.Restart) { return fmt.Errorf("unit %s Restart mismatch", svc.Name) }; if strings.TrimSpace(sections["Install"]["WantedBy"]) != "edge-gateway.target" { return fmt.Errorf("unit %s WantedBy mismatch", svc.Name) } }; return nil }

func checkHealth(order []Service) error { for _, svc := range order { if svc.HealthPath == "" { continue }; data, err := os.ReadFile(svc.HealthPath); if err != nil { return fmt.Errorf("service %s health check failed: %w", svc.Name, err) }; if strings.TrimSpace(string(data)) != "ready" { return fmt.Errorf("service %s health check is not ready", svc.Name) } }; return nil }

func render(root, version string, order []Service, expanded map[string]map[string]string, closures map[string][]string) error {
	configDir := filepath.Join(root,"configs"); if err := os.MkdirAll(configDir,0755); err != nil { return err }; names := []string{}; activation := Activation{ProfileVersion:version}
	for _, svc := range order { names=append(names,svc.Name); keys:=[]string{}; for key := range expanded[svc.Name] { keys=append(keys,key) }; sort.Strings(keys); lines:=[]string{}; for _, key := range keys { lines=append(lines,key+"="+expanded[svc.Name][key]) }; content := ""; if len(lines)>0 { content = strings.Join(lines,"\n")+"\n" }; cfgPath := filepath.Join(configDir,svc.Name+".env"); if err := os.WriteFile(cfgPath,[]byte(content),0644); err != nil { return err }; sum := sha256.Sum256([]byte(content)); activation.Services = append(activation.Services, ActivationService{Name:svc.Name, After:copySlice(svc.After), DependencyClosure:closures[svc.Name], Restart:restartValue(svc.Restart), HealthPath:svc.HealthPath, ConfigSHA256:hex.EncodeToString(sum[:])}) }
	if err := os.WriteFile(filepath.Join(root,"order.txt"), []byte(strings.Join(names,"\n")+"\n"), 0644); err != nil { return err }
	return writeJSONAtomic(filepath.Join(root,"activation.json"), activation)
}

func fingerprint(root string, order []Service) (string,error) { files := []string{filepath.Join(root,"activation.json"), filepath.Join(root,"order.txt")}; configFiles, err := filepath.Glob(filepath.Join(root,"configs","*.env")); if err != nil { return "",err }; sort.Strings(configFiles); files=append(files,configFiles...); h:=sha256.New(); for _, file := range files { data, err := os.ReadFile(file); if err != nil { return "",err }; rel,_ := filepath.Rel(root,file); h.Write([]byte(filepath.ToSlash(rel))); h.Write([]byte{0}); h.Write(data); h.Write([]byte{0}) }; for _, svc := range order { unitPaths, err := unitFiles(svc.Name); if err != nil { return "",err }; for _, file := range unitPaths { data, err := os.ReadFile(file); if err != nil { return "",err }; rel,_ := filepath.Rel("/app",file); h.Write([]byte(filepath.ToSlash(rel))); h.Write([]byte{0}); h.Write(data); h.Write([]byte{0}) } }; return hex.EncodeToString(h.Sum(nil)),nil }
func publish(stage, outDir string) error { backup := outDir+".rollback"; _=os.RemoveAll(backup); if _, err := os.Stat(outDir); err == nil { if err := os.Rename(outDir,backup); err != nil { return err } }; if err := os.Rename(stage,outDir); err != nil { if _, statErr := os.Stat(backup); statErr == nil { _=os.Rename(backup,outDir) }; return err }; return os.RemoveAll(backup) }
func readStatus(path string) (Status,error) { data, err := os.ReadFile(path); if err != nil { return Status{},err }; var status Status; if err := json.Unmarshal(data,&status); err != nil { return Status{},err }; return status,nil }
func appendHistory(path string, entry HistoryEntry) error { if err := os.MkdirAll(filepath.Dir(path),0755); err != nil { return err }; data, err := json.Marshal(entry); if err != nil { return err }; f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644); if err != nil { return err }; defer f.Close(); if _, err := f.Write(append(data,'\n')); err != nil { return err }; return nil }
func writeJSONAtomic(path string, value any) error { if err := os.MkdirAll(filepath.Dir(path),0755); err != nil { return err }; data, err := json.MarshalIndent(value,"","  "); if err != nil { return err }; data=append(data,'\n'); tmp, err := os.CreateTemp(filepath.Dir(path),".json-"); if err != nil { return err }; tmpName := tmp.Name(); if _, err := tmp.Write(data); err != nil { _=tmp.Close(); _=os.Remove(tmpName); return err }; if err := tmp.Close(); err != nil { _=os.Remove(tmpName); return err }; return os.Rename(tmpName,path) }
GO

go build -o /app/bin/gatewayctl ./cmd/gatewayctl

cat > /app/config/site.json <<'JSON'
{
  "profile_version": "2026.07",
  "services": [
    {"name": "network", "env": {"IFACE": "wan0", "MODE": "dhcp"}, "restart": "on-failure", "health_path": "/app/signals/network.ok"},
    {"name": "firewall", "after": ["network"], "env": {"DEFAULT_POLICY": "drop", "NFT_TABLE": "edge", "UPLINK": "${network.IFACE}"}, "restart": "on-failure", "health_path": "/app/signals/firewall.ok"},
    {"name": "watchdog", "after": ["firewall"], "env": {"INTERVAL_SEC": "15", "TARGET": "gateway"}, "restart": "always"},
    {"name": "gateway", "after": ["firewall", "watchdog"], "env": {"LISTEN": "127.0.0.1:8080", "MODE": "edge", "POLICY": "${firewall.DEFAULT_POLICY}"}, "restart": "always", "health_path": "/app/signals/gateway.ok"}
  ]
}
JSON

printf 'ready\n' > /app/signals/network.ok
printf 'ready\n' > /app/signals/firewall.ok
printf 'ready\n' > /app/signals/gateway.ok
rm -f /app/signals/gateway.down

cat > /app/systemd/network.service <<'UNIT'
[Unit]
Description=Edge gateway network preparation

[Service]
EnvironmentFile=/app/run/rendered/configs/network.env
Restart=on-failure

[Install]
WantedBy=edge-gateway.target
UNIT

cat > /app/systemd/firewall.service <<'UNIT'
[Unit]
Description=Edge gateway firewall policy
After=network.service

[Service]
EnvironmentFile=/app/run/rendered/configs/firewall.env
Restart=on-failure

[Install]
WantedBy=edge-gateway.target
UNIT

cat > /app/systemd/watchdog.service <<'UNIT'
[Unit]
Description=Edge gateway watchdog
After=firewall.service

[Service]
EnvironmentFile=/app/run/rendered/configs/watchdog.env
Restart=always

[Install]
WantedBy=edge-gateway.target
UNIT

cat > /app/systemd/gateway.service <<'UNIT'
[Unit]
Description=Edge gateway listener
After=firewall.service watchdog.service

[Service]
EnvironmentFile=/app/run/rendered/configs/gateway.env
Restart=always

[Install]
WantedBy=edge-gateway.target
UNIT

cat > /app/systemd/firewall.service.d/10-policy.conf <<'UNIT'
[Service]
Restart=on-failure
UNIT

cat > /app/systemd/gateway.service.d/20-render.conf <<'UNIT'
[Service]
EnvironmentFile=/app/run/rendered/configs/gateway.env
UNIT

/app/bin/gatewayctl apply --manifest /app/config/site.json --state /app/run/state --out /app/run/rendered
