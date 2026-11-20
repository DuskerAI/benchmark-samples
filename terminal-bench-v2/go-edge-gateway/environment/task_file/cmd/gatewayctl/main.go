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

type Manifest struct {
	Services []Service `json:"services"`
}

type Service struct {
	Name       string            `json:"name"`
	After      []string          `json:"after"`
	Env        map[string]string `json:"env"`
	HealthPath string            `json:"health_path"`
}

type Status struct {
	Generation         int             `json:"generation"`
	LastGoodGeneration int             `json:"last_good_generation"`
	Fingerprint        string          `json:"fingerprint"`
	Services           []StatusService `json:"services"`
}

type StatusService struct {
	Name   string   `json:"name"`
	State  string   `json:"state"`
	Config string   `json:"config"`
	Unit   string   `json:"unit"`
	After  []string `json:"after"`
}

var (
	serviceNameRe = regexp.MustCompile(`^[a-z0-9-]+$`)
	envKeyRe      = regexp.MustCompile(`^[A-Z][A-Z0-9_]*$`)
)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "apply" {
		fmt.Fprintln(os.Stderr, "usage: gatewayctl apply --manifest PATH --state DIR --out DIR")
		os.Exit(2)
	}
	fs := flag.NewFlagSet("apply", flag.ExitOnError)
	manifestPath := fs.String("manifest", "/app/config/site.json", "manifest path")
	stateDir := fs.String("state", "/app/run/state", "state dir")
	outDir := fs.String("out", "/app/run/rendered", "output dir")
	_ = fs.Parse(os.Args[2:])
	if err := apply(*manifestPath, *stateDir, *outDir); err != nil {
		_ = os.MkdirAll(*stateDir, 0o755)
		_ = writeJSONAtomic(filepath.Join(*stateDir, "last_error.json"), map[string]string{"error": err.Error()})
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func apply(manifestPath, stateDir, outDir string) error {
	manifest, err := readManifest(manifestPath)
	if err != nil {
		return err
	}
	order, err := activationOrder(manifest.Services)
	if err != nil {
		return err
	}
	if err := validateUnits(order); err != nil {
		return err
	}
	if err := checkHealth(order); err != nil {
		return err
	}
	parent := filepath.Dir(outDir)
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	stage, err := os.MkdirTemp(parent, ".stage-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	if err := render(stage, order); err != nil {
		return err
	}
	fp, err := fingerprint(stage, order)
	if err != nil {
		return err
	}
	previous, _ := readStatus(filepath.Join(stateDir, "status.json"))
	generation := 1
	if previous.Generation > 0 {
		generation = previous.Generation
		if previous.Fingerprint != fp {
			generation++
		}
	}
	status := Status{Generation: generation, LastGoodGeneration: generation, Fingerprint: fp}
	for _, svc := range order {
		status.Services = append(status.Services, StatusService{
			Name:   svc.Name,
			State:  "healthy",
			Config: filepath.Join(outDir, "configs", svc.Name+".env"),
			Unit:   filepath.Join("/app/systemd", svc.Name+".service"),
			After:  append([]string{}, svc.After...),
		})
	}
	if err := publish(stage, outDir); err != nil {
		return err
	}
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return err
	}
	if err := writeJSONAtomic(filepath.Join(stateDir, "status.json"), status); err != nil {
		return err
	}
	if err := os.Remove(filepath.Join(stateDir, "last_error.json")); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	return nil
}

func readManifest(path string) (Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Manifest{}, err
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return Manifest{}, err
	}
	if len(manifest.Services) == 0 {
		return Manifest{}, errors.New("manifest has no services")
	}
	seen := map[string]bool{}
	for _, svc := range manifest.Services {
		if !serviceNameRe.MatchString(svc.Name) {
			return Manifest{}, fmt.Errorf("invalid service name %q", svc.Name)
		}
		if seen[svc.Name] {
			return Manifest{}, fmt.Errorf("duplicate service %q", svc.Name)
		}
		seen[svc.Name] = true
		seenDeps := map[string]bool{}
		for _, dep := range svc.After {
			if dep == svc.Name {
				return Manifest{}, fmt.Errorf("service %s depends on itself", svc.Name)
			}
			if seenDeps[dep] {
				return Manifest{}, fmt.Errorf("service %s repeats dependency %s", svc.Name, dep)
			}
			seenDeps[dep] = true
		}
		if svc.HealthPath != "" {
			clean := filepath.Clean(svc.HealthPath)
			if !filepath.IsAbs(svc.HealthPath) || clean != svc.HealthPath || !strings.HasPrefix(clean, "/app/signals/") {
				return Manifest{}, fmt.Errorf("invalid health path for %s", svc.Name)
			}
		}
		for key, value := range svc.Env {
			if !envKeyRe.MatchString(key) {
				return Manifest{}, fmt.Errorf("invalid env key %q", key)
			}
			if strings.Contains(value, "\n") || strings.Contains(value, "\r") {
				return Manifest{}, fmt.Errorf("invalid env value for %s", key)
			}
		}
	}
	return manifest, nil
}

func validateUnits(order []Service) error {
	for _, svc := range order {
		path := filepath.Join("/app/systemd", svc.Name+".service")
		data, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("unit file for %s: %w", svc.Name, err)
		}
		sections := parseUnit(data)
		unit := sections["Unit"]
		service := sections["Service"]
		install := sections["Install"]
		expectedAfter := ""
		if len(svc.After) > 0 {
			parts := make([]string, 0, len(svc.After))
			for _, dep := range svc.After {
				parts = append(parts, dep+".service")
			}
			expectedAfter = strings.Join(parts, " ")
		}
		if strings.TrimSpace(unit["After"]) != expectedAfter {
			return fmt.Errorf("unit %s After mismatch", svc.Name)
		}
		expectedEnv := filepath.Join("/app/run/rendered/configs", svc.Name+".env")
		if strings.TrimSpace(service["EnvironmentFile"]) != expectedEnv {
			return fmt.Errorf("unit %s EnvironmentFile mismatch", svc.Name)
		}
		if strings.TrimSpace(install["WantedBy"]) != "edge-gateway.target" {
			return fmt.Errorf("unit %s WantedBy mismatch", svc.Name)
		}
	}
	return nil
}

func parseUnit(data []byte) map[string]map[string]string {
	sections := map[string]map[string]string{}
	current := ""
	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			current = strings.TrimSuffix(strings.TrimPrefix(line, "["), "]")
			if sections[current] == nil {
				sections[current] = map[string]string{}
			}
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if ok && current != "" {
			sections[current][strings.TrimSpace(key)] = strings.TrimSpace(value)
		}
	}
	return sections
}

func activationOrder(services []Service) ([]Service, error) {
	byName := map[string]Service{}
	for _, svc := range services {
		byName[svc.Name] = svc
	}
	for _, svc := range services {
		for _, dep := range svc.After {
			if _, ok := byName[dep]; !ok {
				return nil, fmt.Errorf("service %s depends on missing service %s", svc.Name, dep)
			}
		}
	}
	done := map[string]bool{}
	order := make([]Service, 0, len(services))
	for len(order) < len(services) {
		progress := false
		for _, svc := range services {
			if done[svc.Name] {
				continue
			}
			ready := true
			for _, dep := range svc.After {
				if !done[dep] {
					ready = false
					break
				}
			}
			if ready {
				order = append(order, svc)
				done[svc.Name] = true
				progress = true
			}
		}
		if !progress {
			return nil, errors.New("dependency cycle detected")
		}
	}
	return order, nil
}

func checkHealth(order []Service) error {
	for _, svc := range order {
		if svc.HealthPath == "" {
			continue
		}
		data, err := os.ReadFile(svc.HealthPath)
		if err != nil {
			return fmt.Errorf("service %s health check failed: %w", svc.Name, err)
		}
		if strings.TrimSpace(string(data)) != "ready" {
			return fmt.Errorf("service %s health check is not ready", svc.Name)
		}
	}
	return nil
}

func render(root string, order []Service) error {
	configDir := filepath.Join(root, "configs")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		return err
	}
	names := make([]string, 0, len(order))
	for _, svc := range order {
		names = append(names, svc.Name)
		keys := make([]string, 0, len(svc.Env))
		for key := range svc.Env {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		lines := make([]string, 0, len(keys))
		for _, key := range keys {
			lines = append(lines, key+"="+svc.Env[key])
		}
		content := ""
		if len(lines) > 0 {
			content = strings.Join(lines, "\n") + "\n"
		}
		if err := os.WriteFile(filepath.Join(configDir, svc.Name+".env"), []byte(content), 0o644); err != nil {
			return err
		}
	}
	return os.WriteFile(filepath.Join(root, "order.txt"), []byte(strings.Join(names, "\n")+"\n"), 0o644)
}

func fingerprint(root string, order []Service) (string, error) {
	files := []string{filepath.Join(root, "order.txt")}
	configFiles, err := filepath.Glob(filepath.Join(root, "configs", "*.env"))
	if err != nil {
		return "", err
	}
	sort.Strings(configFiles)
	files = append(files, configFiles...)
	h := sha256.New()
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			return "", err
		}
		rel, _ := filepath.Rel(root, file)
		h.Write([]byte(filepath.ToSlash(rel)))
		h.Write([]byte{0})
		h.Write(data)
		h.Write([]byte{0})
	}
	for _, svc := range order {
		file := filepath.Join("/app/systemd", svc.Name+".service")
		data, err := os.ReadFile(file)
		if err != nil {
			return "", err
		}
		h.Write([]byte("systemd/" + svc.Name + ".service"))
		h.Write([]byte{0})
		h.Write(data)
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func publish(stage, outDir string) error {
	backup := outDir + ".rollback"
	_ = os.RemoveAll(backup)
	if _, err := os.Stat(outDir); err == nil {
		if err := os.Rename(outDir, backup); err != nil {
			return err
		}
	}
	if err := os.Rename(stage, outDir); err != nil {
		if _, statErr := os.Stat(backup); statErr == nil {
			_ = os.Rename(backup, outDir)
		}
		return err
	}
	return os.RemoveAll(backup)
}

func readStatus(path string) (Status, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Status{}, err
	}
	var status Status
	if err := json.Unmarshal(data, &status); err != nil {
		return Status{}, err
	}
	return status, nil
}

func writeJSONAtomic(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	tmp, err := os.CreateTemp(filepath.Dir(path), ".json-")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	return os.Rename(tmpName, path)
}
