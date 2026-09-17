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
		"internal/protocol",
		"internal/protocol/anthropic/messages",
		"internal/protocol/bedrock/converse",
		"internal/protocol/gemini/generatecontent",
		"internal/protocol/openai/chatcompletions",
		"internal/protocol/openai/realtime",
		"internal/protocol/openai/responses",
		"internal/receipt",
		"internal/service",
		"internal/service/azure",
		"internal/service/bedrock",
		"internal/service/generic",
		"internal/service/huggingface",
		"internal/service/vertex",
		"internal/telemetry",
		"internal/testkit",
		"internal/upstream",
		"internal/upstream/eventstream",
		"internal/upstream/httpclient",
		"internal/upstream/pool",
		"internal/upstream/sse",
		"internal/upstream/websocket",
		"ports",
		"integration",
	}
	for _, relative := range required {
		path := filepath.Join(source, relative)
		if info, err := os.Stat(path); err != nil || !info.IsDir() {
			t.Errorf("required owner directory %q is missing", relative)
		}
	}
	for _, relative := range []string{
		"protocols", "connectors", "upstream", "internal/capability", "internal/plan",
		"internal/protocol/registry.go", "internal/service/registry.go",
	} {
		if _, err := os.Stat(filepath.Join(source, relative)); err == nil {
			t.Errorf("removed owner or registry still exists: %q", relative)
		} else if !os.IsNotExist(err) {
			t.Errorf("stat removed owner %q: %v", relative, err)
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
