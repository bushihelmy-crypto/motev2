package architecture_test

import (
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
)

const modulePath = "github.com/bushihelmy-crypto/motev2/mote-runtime/gateway"

func projectRoot(t *testing.T) string {
	t.Helper()
	_, source, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(source), "..", "..", ".."))
}

func sourceRoot(t *testing.T) string {
	return filepath.Join(projectRoot(t), "src")
}

func TestScaffoldHasOneModuleAndExplicitOwners(t *testing.T) {
	root := projectRoot(t)
	source := sourceRoot(t)
	required := []string{
		"api",
		"cmd/gateway",
		"internal/application",
		"internal/admission",
		"internal/architecture",
		"internal/cache",
		"internal/cache/prompt",
		"internal/cache/result",
		"internal/complexity",
		"internal/model",
		"internal/plan",
		"internal/protocol",
		"internal/receipt",
		"internal/service",
		"internal/telemetry",
		"internal/testkit",
		"internal/upstream",
		"protocols",
		"connectors",
		"upstream",
		"ports",
		"integration",
	}
	for _, relative := range required {
		path := filepath.Join(source, relative)
		if info, err := os.Stat(path); err != nil || !info.IsDir() {
			t.Errorf("required owner directory %q is missing", relative)
		}
	}

	var modules []string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() && (info.Name() == ".cache" || info.Name() == ".tools" || info.Name() == ".git") {
			return filepath.SkipDir
		}
		if !info.IsDir() && info.Name() == "go.mod" {
			modules = append(modules, path)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(modules) != 1 || modules[0] != filepath.Join(root, "src", "go.mod") {
		t.Fatalf("expected exactly one module at src/go.mod, found %v", modules)
	}
}

func TestOwnerlessAndOutOfScopePackagesAreRejected(t *testing.T) {
	forbidden := map[string]bool{
		"common":  true,
		"helper":  true,
		"helpers": true,
		"misc":    true,
		"shared":  true,
		"util":    true,
		"utils":   true,
		"mcp":     true,
		"router":  true,
		"agent":   true,
	}
	var violations []string
	err := filepath.Walk(sourceRoot(t), func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() {
			return nil
		}
		if forbidden[strings.ToLower(info.Name())] {
			relative, _ := filepath.Rel(sourceRoot(t), path)
			violations = append(violations, filepath.ToSlash(relative))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(violations)
	if len(violations) != 0 {
		t.Fatalf("ownerless or out-of-scope packages found: %v", violations)
	}
}

func TestEveryProductionDirectoryHasPackageDocumentation(t *testing.T) {
	root := sourceRoot(t)
	var violations []string
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if !info.IsDir() || info.Name() == "testdata" {
			return nil
		}
		entries, readErr := os.ReadDir(path)
		if readErr != nil {
			return readErr
		}
		hasGo := false
		hasDoc := false
		for _, entry := range entries {
			if entry.IsDir() {
				continue
			}
			if entry.Name() == "doc.go" {
				hasDoc = true
			}
			if strings.HasSuffix(entry.Name(), ".go") && !strings.HasSuffix(entry.Name(), "_test.go") {
				hasGo = true
			}
		}
		if hasGo && !hasDoc {
			relative, _ := filepath.Rel(root, path)
			violations = append(violations, filepath.ToSlash(relative))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(violations) != 0 {
		t.Fatalf("production package directories need doc.go: %v", violations)
	}
}
